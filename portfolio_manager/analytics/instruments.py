"""Instrument data from Yahoo Finance: prices in the account currency, performance and the ETF catalog of each
account.
"""

import logging
import time
from collections import Counter
from datetime import date, timedelta

import pandas as pd

from portfolio_manager.analytics.metrics import PerfDataTabs, compute_portfolio_metrics
from portfolio_manager.config.accounts import Account, list_accounts
from portfolio_manager.config.settings import RESULTS_DIR
from portfolio_manager.degiro.charts import load_fx_rates
from portfolio_manager.degiro.definitions import Exchanges, ProductTypes
from portfolio_manager.degiro.products import load_portfolio_products
from portfolio_manager.market_data.history import (
    listing_currencies,
    load_adjusted_closes,
    major_currency,
    refresh_history,
    search_listing,
)
from portfolio_manager.market_data.yahoo import YF_PROD_INFO_LABEL, YFinHistCols, YFinInfoCols
from portfolio_manager.storage.files import PD_DATA_TYPES, load_df_dict_from_excel, save_df_dict_to_excel
from portfolio_manager.storage.models import Product
from portfolio_manager.storage.queries import (
    query_degiro_yahoo_map,
    query_products,
    query_yahoo_instruments,
    query_yahoo_prices,
    upsert_degiro_yahoo_map,
)
from portfolio_manager.storage.tables import derived_folder, has_tables, load_tables, save_tables

logger = logging.getLogger(__name__)

MAX_ETF_CATALOG_SIZE = 250
CATALOG_PERF_LABEL = 'Performance'
# Degiro exchanges of the ETFs of the catalog
CATALOG_EXCHANGES = [Exchanges.XET, Exchanges.SWX, Exchanges.MIL, Exchanges.EAM]
TRADED_VALUE_DAYS = 63  # trading days (about 3 months) of the average traded value that ranks the catalog ETFs
MAX_DAYS_WITHOUT_PRICE = 30  # listings without a price for longer are left out of the catalog
# Yahoo information fields (rows of the information tables of the catalogs) and their columns in the database
INFO_COLUMNS = {
    YFinInfoCols.currency.value: 'currency',
    YFinInfoCols.isin.value: 'isin',
    YFinInfoCols.name_short.value: 'name_short',
    YFinInfoCols.name_long.value: 'name_long',
    YFinInfoCols.quote_type.value: 'quote_type',
    YFinInfoCols.exchange.value: 'exchange',
    YFinInfoCols.market.value: 'market',
    YFinInfoCols.region.value: 'region',
}


class InstrPerfTableCols:
    """Columns of the instruments table."""

    ticker = 'Ticker'
    isin = 'ISIN'
    name = 'Name'
    volume = 'Traded value (3M)'  # average daily traded value of the last 3 months, account currency


def prices_to_base_curr(account: Account, price_df: pd.DataFrame, curr_info: PD_DATA_TYPES | list[str]) -> pd.DataFrame:
    """Amounts (prices, traded values) converted to the account currency, given the Yahoo currency of each column
    (amounts quoted in hundredths of a currency, e.g. pence, are converted too).
    """
    if not isinstance(curr_info, pd.Series | list):
        raise TypeError
    majors = [major_currency(c) if isinstance(c, str) and c else (None, 1.0) for c in curr_info]
    curr_foreign_lst = sorted({c for c, _ in majors if c is not None and c != account.currency})
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst, account=account, index=price_df.index)
    fx_rates_df = fx_rates_df.reindex(index=price_df.index).ffill()
    # convert to base currency; a ticker appearing in several columns (e.g. several exchanges) is averaged
    price_base_df = pd.DataFrame()
    for ticker, (curr, factor) in zip(price_df.columns, majors, strict=True):
        if ticker in price_base_df:
            continue
        try:
            fx_rate = fx_rates_df.loc[price_df.index, f'{curr}/{account.currency}']
        except KeyError:
            logger.warning('Missing foreign exchange historical time series for %s/%s', curr, account.currency)
            continue
        ser = price_df[ticker]
        if isinstance(ser, pd.DataFrame):
            ser = ser.mean(axis=1)
        ser = ser.mul(fx_rate, axis=0).mul(factor).rename(ticker)
        price_base_df = pd.concat([price_base_df, ser], axis=1)
    price_base_df.index = pd.to_datetime(price_base_df.index)
    price_base_df = price_base_df.sort_index()
    return price_base_df


def stored_adjusted_prices(account: Account, tickers: list[str]) -> pd.DataFrame:
    """Stored adjusted closes of Yahoo Finance listings in the account currency: date x ticker (listings without
    history or currency are left out).
    """
    closes = load_adjusted_closes(tickers)
    currencies = listing_currencies(list(closes.columns))
    tickers = [t for t in closes.columns if t in currencies]
    if not tickers:
        return pd.DataFrame()
    return prices_to_base_curr(account=account, price_df=closes[tickers], curr_info=[currencies[t] for t in tickers])


def instruments_info(tickers: list[str]) -> pd.DataFrame:
    """Information of Yahoo Finance listings: Yahoo field x ticker (the layout of the catalog tables)."""
    df = query_yahoo_instruments(tickers).reindex(tickers)
    info = pd.DataFrame({field: df[column] for field, column in INFO_COLUMNS.items()}).T
    info.loc[YFinInfoCols.symbol.value] = tickers
    return info


def fetch_portfolio_instr_adj_prices(account: Account) -> pd.DataFrame:
    """Adjusted prices of the products of an account, in the account currency: date x product symbol."""
    from portfolio_manager.backtest.adjusted_prices import get_portfolio_adj_prices

    prices = get_portfolio_adj_prices(account=account).prices
    symbols = load_portfolio_products(account=account).set_index('id')['symbol']
    return prices.rename(columns=symbols.to_dict())


def compute_product_performance(
    adj_close_df: PD_DATA_TYPES, volume_df: PD_DATA_TYPES | None = None, prod_info_df: PD_DATA_TYPES | None = None
) -> pd.DataFrame:
    """Performance metrics of each price series (and its volume if given)."""
    perf_metrics_df = pd.DataFrame()
    for ticker in adj_close_df.columns:
        isin_str = f' ({prod_info_df.loc[YFinInfoCols.isin.value, ticker]})' if prod_info_df is not None else ''
        logger.debug('Computing performance metrics for %s%s', ticker, isin_str)
        # compute performance metrics
        try:
            results_dict = compute_portfolio_metrics(
                nav=adj_close_df[ticker].dropna(), compute_hist_metrics=False, print_results=False
            )
        except ValueError:
            continue
        # add dollar volume
        if volume_df is not None:
            if ticker in volume_df.columns:
                volume_mean_90 = int(volume_df[ticker][volume_df[ticker].notnull()].values[-1])
                results_dict[PerfDataTabs.RISK_METRICS][InstrPerfTableCols.volume] = volume_mean_90
        if prod_info_df is not None:
            isin = prod_info_df.loc[YFinInfoCols.isin.value, ticker]
            results_dict[PerfDataTabs.RISK_METRICS][InstrPerfTableCols.isin] = isin
            name = prod_info_df.loc[YFinInfoCols.name_long.value, ticker]
            results_dict[PerfDataTabs.RISK_METRICS][InstrPerfTableCols.name] = name
        perf_metrics_df = pd.concat([perf_metrics_df, results_dict[PerfDataTabs.RISK_METRICS]], axis=1)
    perf_metrics_df = perf_metrics_df.T.copy()
    perf_metrics_df.index.name = InstrPerfTableCols.ticker
    perf_metrics_df = perf_metrics_df.reset_index().copy()
    return perf_metrics_df


def compute_single_etf_performance(isin: str) -> pd.DataFrame:
    """Performance metrics of one ETF (its Yahoo Finance listing is searched, downloaded and stored)."""
    # ticker and name from the Degiro catalog (if listed) help picking the right Yahoo Finance listing
    prod_df = query_products(product_isin=isin, product_type=ProductTypes.ETF)
    ticker = prod_df[Product.symbol.name].iloc[0] if not prod_df.empty else None
    name = prod_df[Product.name.name].iloc[0] if not prod_df.empty else None
    listing = search_listing(search=isin, ticker=ticker, name=name)
    if listing is None:
        return pd.DataFrame()
    df = compute_product_performance(
        adj_close_df=load_adjusted_closes([listing]), prod_info_df=instruments_info([listing])
    )
    logger.debug('Performance of %s:\n%s', isin, df)
    return df


def compute_portfolio_instruments_performance():
    """Performance metrics of the instruments of every account."""
    for account in list_accounts():
        close_adj_df = fetch_portfolio_instr_adj_prices(account=account)
        perf_metrics_df = pd.DataFrame()
        for instr in close_adj_df.columns:
            results_dict = compute_portfolio_metrics(nav=close_adj_df[instr])
            perf_metrics_df = pd.concat([perf_metrics_df, results_dict[PerfDataTabs.RISK_METRICS]], axis=1)
        save_df_dict_to_excel(
            df_dict={PerfDataTabs.RISK_METRICS: perf_metrics_df, PerfDataTabs.PRICES: close_adj_df},
            folder_name=RESULTS_DIR,
            file_name=f'{account.name}_instr',
        )


def catalog_products() -> pd.DataFrame:
    """Tradable ETFs of the Degiro catalog on the exchanges of the catalog, one per ISIN and symbol."""
    etf_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)
    etf_info_df = etf_info_df[etf_info_df[Product.exchange_id.name].isin([x.value for x in CATALOG_EXCHANGES])]
    return etf_info_df.drop_duplicates(subset=['isin', 'symbol'], keep='first')


def fetch_etf_catalog_data(full: bool = False):
    """Download the Yahoo Finance history of the ETFs of the catalog into the database: the listing of an ETF is
    searched once, then only the new days are downloaded (the whole history again with full). An interrupted run
    resumes where it stopped.
    """
    products = catalog_products()
    listings = query_degiro_yahoo_map(products['id'].to_list())
    results = Counter()
    for n, product in enumerate(products.itertuples()):
        ticker = listings.get(product.id)
        try:
            if ticker is None:
                ticker = search_listing(search=product.isin, ticker=product.symbol, name=product.name)
                if ticker is not None:
                    upsert_degiro_yahoo_map(product_ids=[product.id], ticker=ticker)
                result = 'new' if ticker is not None else 'not found'
            else:
                result = refresh_history(ticker, full=full)
        except Exception as e:  # any download problem: the ETF is retried at the next run
            logger.warning('Yahoo Finance download failed for %s: %s', product.isin, e)
            result = 'failed'
        results[result] += 1
        logger.info('(%d/%d) %s %s: %s', n + 1, len(products), product.isin, ticker or '', result)
        time.sleep(0.2)  # spread the requests to Yahoo Finance
    logger.info('ETF catalog downloaded: %s', ', '.join(f'{k} {v}' for k, v in results.items()))


def traded_values(account: Account, tickers: list[str]) -> pd.DataFrame:
    """Average daily traded value (close x volume) of the last TRADED_VALUE_DAYS trading days of each listing, in the
    account currency: date x ticker (listings without a recent price are left out).
    """
    recent = query_yahoo_prices(tickers, start=date.today() - timedelta(days=200), columns=['close', 'volume'])
    recent['traded'] = recent['close'] * recent['volume']
    traded = recent.pivot(index='date', columns='ticker', values='traded')
    last_day = pd.Timestamp(date.today() - timedelta(days=MAX_DAYS_WITHOUT_PRICE))
    traded = traded.loc[:, [t for t in traded.columns if traded[t].last_valid_index() >= last_day]]
    currencies = listing_currencies(list(traded.columns))
    traded = traded[[t for t in traded.columns if t in currencies]]
    traded = prices_to_base_curr(account=account, price_df=traded, curr_info=[currencies[t] for t in traded.columns])
    # average over the trading days of each listing (exchanges have different holidays)
    return pd.DataFrame({t: traded[t].dropna().rolling(TRADED_VALUE_DAYS).mean() for t in traded.columns})


def build_etf_catalog_data(account: Account) -> dict[YFinHistCols, pd.DataFrame]:
    """Build and save the ETF catalog of an account from the stored history: the ETFs with the highest traded value,
    their adjusted prices in the account currency and their performance metrics.
    """
    tickers = sorted(set(query_degiro_yahoo_map(catalog_products()['id'].to_list()).values()))
    traded_df = traded_values(account, tickers)
    ranking = traded_df.apply(lambda x: x.dropna().iloc[-1] if x.notna().any() else 0.0)
    most_liquid = ranking.sort_values(ascending=False).index[:MAX_ETF_CATALOG_SIZE].to_list()
    close_adj_df = stored_adjusted_prices(account, most_liquid)
    most_liquid = [t for t in most_liquid if t in close_adj_df.columns]
    data_dict = {
        YFinHistCols.adj_close: close_adj_df[most_liquid],
        YFinHistCols.volume: traded_df[most_liquid],
        YF_PROD_INFO_LABEL: instruments_info(most_liquid),
    }
    data_dict[CATALOG_PERF_LABEL] = compute_catalog_performance_df(data_dict)
    save_etf_catalog_data(account=account, data_dict=data_dict)
    return data_dict


def compute_catalog_performance_df(data_dict: dict[str | YFinHistCols, pd.DataFrame]) -> pd.DataFrame:
    """Performance metrics of the ETFs of a catalog."""
    return compute_product_performance(
        adj_close_df=data_dict[YFinHistCols.adj_close],
        volume_df=data_dict[YFinHistCols.volume],
        prod_info_df=data_dict[YF_PROD_INFO_LABEL],
    )


def compute_catalog_performance(account: Account) -> pd.DataFrame:
    """Add the performance metrics to an existing catalog, from its saved data only."""
    data_dict = load_etf_catalog_data(account=account)
    data_dict[CATALOG_PERF_LABEL] = compute_catalog_performance_df(data_dict)
    save_etf_catalog_data(account=account, data_dict=data_dict)
    return data_dict[CATALOG_PERF_LABEL]


CATALOGS = 'catalogs'  # kind of derived result (folder of the Parquet tables)


def catalog_file_name(account: Account) -> str:
    """Name of the ETF catalog files of an account."""
    return f'{account.name}_catalog'


def save_etf_catalog_data(account: Account, data_dict: dict[str | YFinHistCols, pd.DataFrame]):
    """Save the ETF catalog of an account: Parquet tables and Excel file."""
    data_dict_renamed = {str(k): data_dict[k] for k in data_dict}
    save_df_dict_to_excel(df_dict=data_dict_renamed, folder_name=RESULTS_DIR, file_name=catalog_file_name(account))
    save_etf_catalog_tables(account, data_dict_renamed)


def save_etf_catalog_tables(account: Account, data_dict: dict[str, pd.DataFrame]):
    """Save the Parquet tables of the ETF catalog of an account (keys: sheet names)."""
    save_tables(derived_folder(CATALOGS, catalog_file_name(account)), data_dict)


def load_etf_catalog_data(account: Account) -> dict[str | YFinHistCols, pd.DataFrame]:
    """Keys: YFinHistCols for the price and volume sheets, YF_PROD_INFO_LABEL, and CATALOG_PERF_LABEL if computed."""
    folder = derived_folder(CATALOGS, catalog_file_name(account))
    if has_tables(folder):
        data_dict = load_tables(folder)
    else:  # saved before the Parquet tables
        data_dict = load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=catalog_file_name(account))
    return {YFinHistCols.get_entry_by_val(k): data_dict[k] for k in data_dict}
