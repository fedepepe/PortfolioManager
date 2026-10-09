"""Instrument data from Yahoo Finance: prices in the account currency, performance and the ETF catalog of each
account.
"""

import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd

from portfolio_manager.analytics.metrics import Metrics, PerfDataTabs, compute_portfolio_metrics
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
    traded_value,
)
from portfolio_manager.market_data.yahoo import YFinInfoCols
from portfolio_manager.storage.files import PD_DATA_TYPES, save_df_dict_to_excel, save_df_to_excel
from portfolio_manager.storage.models import Product
from portfolio_manager.storage.queries import (
    query_degiro_yahoo_map,
    query_etf_catalog,
    query_products,
    query_yahoo_instruments,
    save_etf_catalog,
    upsert_degiro_yahoo_map,
)

logger = logging.getLogger(__name__)

MAX_ETF_CATALOG_SIZE = 250
# Degiro exchanges of the ETFs of the catalog
CATALOG_EXCHANGES = [Exchanges.XET, Exchanges.SWX, Exchanges.MIL, Exchanges.EAM]
TRADED_VALUE_PERIOD = pd.DateOffset(months=3)  # average daily traded value over the last 3 calendar months
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


# metrics of the catalog ETFs and their columns in the etf_catalog table
CATALOG_METRICS = {
    Metrics.TOTAL_RETURN: 'total_return',
    Metrics.PA_RETURN: 'pa_return',
    Metrics.LAST_YEAR_RETURN: 'return_1y',
    Metrics.ANN_3Y_RETURN: 'return_3y',
    Metrics.ANN_5Y_RETURN: 'return_5y',
    Metrics.VOLATILITY: 'volatility',
    Metrics.SHARPE_RATIO: 'sharpe_ratio',
    Metrics.SORTINO_RATIO: 'sortino_ratio',
    Metrics.BEST_MONTH: 'best_month',
    Metrics.WORST_MONTH: 'worst_month',
    Metrics.MAX_DD: 'max_drawdown',
    Metrics.SKEWNESS: 'skewness',
}


class InstrPerfTableCols:
    """Columns of the instruments table."""

    ticker = 'Ticker'
    isin = 'ISIN'
    name = 'Name'
    volume = 'Traded value (3M)'  # average daily traded value of the last 3 months, in the currency of the listing
    currency = 'Currency'


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


def compute_product_performance(adj_close_df: PD_DATA_TYPES, prod_info_df: PD_DATA_TYPES | None = None) -> pd.DataFrame:
    """Performance metrics of each price series: one row per ticker (with ISIN and name if the information is given)."""
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


def average_traded_values(tickers: list[str]) -> pd.DataFrame:
    """Average daily traded value of the last 3 months (TRADED_VALUE_PERIOD before its last day) of each listing, in
    the currency of the listing: columns traded_value and currency by ticker. Listings without a price in the last
    MAX_DAYS_WITHOUT_PRICE days are left out.
    """
    recent = pd.Timestamp(date.today() - timedelta(days=MAX_DAYS_WITHOUT_PRICE))
    traded, currencies = traded_value(tickers, start=(recent - TRADED_VALUE_PERIOD).date())
    rows = {}
    for ticker in traded.columns:
        values = traded[ticker].dropna()
        if not values.empty and values.index[-1] >= recent:
            last_months = values[values.index > values.index[-1] - TRADED_VALUE_PERIOD]
            rows[ticker] = {'traded_value': last_months.mean(), 'currency': currencies[ticker]}
    return pd.DataFrame.from_dict(rows, orient='index', columns=['traded_value', 'currency'])


def rank_by_traded_value(account: Account, averages: pd.DataFrame) -> list[str]:
    """Tickers by decreasing average traded value; the values, in different currencies, are compared at the latest
    exchange rates, so the order is the same whatever the currency of the account.
    """
    foreign = sorted(set(averages['currency']) - {account.currency})
    fx_rates_df = load_fx_rates(account=account, curr_foreign_lst=foreign, index=pd.DatetimeIndex([]))
    latest = fx_rates_df.ffill().iloc[-1] if not fx_rates_df.empty else pd.Series(dtype=float)
    rates = averages['currency'].map(lambda c: 1.0 if c == account.currency else latest.get(f'{c}/{account.currency}'))
    return averages['traded_value'].mul(rates.astype(float)).dropna().sort_values(ascending=False).index.to_list()


def build_etf_catalog_data(account: Account) -> pd.DataFrame:
    """Build and save the ETF catalog of an account from the stored history: the MAX_ETF_CATALOG_SIZE ETFs with the
    highest average traded value and their performance metrics in the account currency (table etf_catalog, with an
    Excel copy in the results folder).
    """
    tickers = sorted(set(query_degiro_yahoo_map(catalog_products()['id'].to_list()).values()))
    averages = average_traded_values(tickers)
    most_traded = rank_by_traded_value(account, averages)[:MAX_ETF_CATALOG_SIZE]
    perf_df = compute_product_performance(stored_adjusted_prices(account, most_traded))
    perf_df = perf_df.set_index(InstrPerfTableCols.ticker)
    catalog = pd.DataFrame({'ticker': [t for t in most_traded if t in perf_df.index]})
    catalog['rank'] = range(1, len(catalog) + 1)
    catalog['traded_value'] = averages.loc[catalog['ticker'], 'traded_value'].to_numpy()
    catalog['currency'] = averages.loc[catalog['ticker'], 'currency'].to_numpy()
    for metric, column in CATALOG_METRICS.items():
        catalog[column] = perf_df.loc[catalog['ticker'], metric.name].to_numpy()
    catalog['built_at'] = datetime.now()
    save_etf_catalog(account.name, catalog)
    save_df_to_excel(df=catalog_table(account), file_name=catalog_file_name(account), folder_name=RESULTS_DIR)
    return catalog


def catalog_file_name(account: Account) -> str:
    """Name of the Excel copy of the ETF catalog of an account."""
    return f'{account.name}_catalog'


def catalog_table(account: Account) -> pd.DataFrame:
    """ETF catalog of an account as shown: ticker, ISIN, name, traded value, currency and metrics (labels), by rank."""
    catalog = query_etf_catalog(account.name)
    info = query_yahoo_instruments(catalog['ticker'].to_list()).reindex(catalog['ticker'])
    table = pd.DataFrame(
        {
            InstrPerfTableCols.ticker: catalog['ticker'],
            InstrPerfTableCols.isin: info['isin'].to_numpy(),
            InstrPerfTableCols.name: info['name_long'].fillna(info['name_short']).to_numpy(),
            InstrPerfTableCols.volume: catalog['traded_value'],
            InstrPerfTableCols.currency: catalog['currency'],
        }
    )
    for metric, column in CATALOG_METRICS.items():
        table[metric.name] = catalog[column]
    return table
