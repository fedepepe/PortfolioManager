import logging
import time
from difflib import SequenceMatcher

import pandas as pd

from config.accounts import Accounts
from config.definitions import DEFAULT_DATA_FREQ, RESULTS_DIR
from database.sql import (
    query_products,
    query_yahoo_finance_hist_data,
    query_yahoo_finance_prod_info,
)
from database.table_definitions import Product
from degiro.charts import load_fx_rates, load_portfolio_products
from degiro.degiro_definitions import Exchanges, ProductTypes
from engines.reporting import PerfDataTabs, compute_portfolio_metrics
from utils.file_utils import PD_DATA_TYPES, load_df_dict_from_excel, save_df_dict_to_excel
from yahoo_finance.yahoo_finance import YF_PROD_INFO_LABEL, YFinHistCols, YFinInfoCols, search_fetch_history
from yahoo_finance.yahoo_finance import Exchanges as ExchangesYF

logger = logging.getLogger(__name__)

MAX_ETF_CATALOG_SIZE = 250
CATALOG_PERF_LABEL = 'Performance'


class InstrPerfTableCols:
    ticker = 'Ticker'
    isin = 'ISIN'
    name = 'Name'
    volume = 'Volume'


def is_missing(value) -> bool:
    if isinstance(value, str):
        return not value.strip()
    return value is None or pd.isna(value)


def choose_ticker(tickers: list[str], ticker: str | None = None) -> str:
    # follow the priority order of exchanges: first {ticker}.{exchange}, then any ticker listed on an exchange
    for prefix in ([ticker] if ticker is not None else []) + [None]:
        for x in ExchangesYF:
            for t in tickers:
                if t.endswith(f'.{x.code}') and (prefix is None or t == f'{prefix}.{x.code}'):
                    return t
    return tickers[0]


def fetch_instr_hist_data(
    isin_lst: str | list[str],
    columns: str | YFinHistCols | list[str] | list[YFinHistCols],
    ticker_lst: str | list[str] | None = None,
    name_lst: str | list[str] | None = None,
    freq: str = DEFAULT_DATA_FREQ,
) -> dict[str | YFinHistCols, pd.DataFrame | pd.Series]:
    if isinstance(isin_lst, str):
        isin_lst = [isin_lst]
    if ticker_lst is None:
        ticker_lst = [None] * len(isin_lst)
    elif isinstance(ticker_lst, str):
        ticker_lst = [ticker_lst]
    if name_lst is None:
        name_lst = [None] * len(ticker_lst)
    elif isinstance(name_lst, str):
        name_lst = [name_lst]
    if isinstance(columns, str) or isinstance(columns, YFinHistCols):
        columns = [columns]
    columns_ext = columns + [YF_PROD_INFO_LABEL]
    data_dict = {col: pd.DataFrame() for col in columns_ext}
    for n, isin in enumerate(isin_lst):
        isin = None if is_missing(isin) else isin
        ticker = None if is_missing(ticker_lst[n]) else ticker_lst[n]
        name = None if is_missing(name_lst[n]) else name_lst[n]
        if isin is None and ticker is None:
            continue
        # 1. try with isin
        data_single = search_fetch_history(isin=isin, columns=columns) if isin is not None else None
        # 2. if no results is given, try with ticker
        if data_single is None or all([data_single[col].empty for col in columns]):
            if ticker is None:
                continue
            data_single = search_fetch_history(ticker=ticker, columns=columns)
        # 3. if still no results is given, give up
        if data_single is None or all([df.empty for df in data_single.values()]):
            continue
        # 4. restrict to tickers that appear in all dataframes
        tickers_restrict = list(set.intersection(*map(set, [data_single[key].columns for key in data_single])))
        for key in data_single:
            data_single[key] = data_single[key].loc[:, tickers_restrict]
        # 5. restrict to tickers with name matching
        if len(tickers_restrict) > 1 and name is not None:
            names_long = data_single[YF_PROD_INFO_LABEL].loc[YFinInfoCols.name_long.value].values
            name_match = [SequenceMatcher(None, name, name_long).ratio() for name_long in names_long]
            ticker_match = data_single[YF_PROD_INFO_LABEL].columns[name_match.index(max(name_match))]
            for key in data_single:
                data_single[key] = data_single[key].loc[:, [ticker_match]]
        if all([df.empty for df in data_single.values()]):
            continue
        # 6. choose one ticker for all columns according to the priority assigned to exchanges
        for col in columns_ext:
            if data_single[col].shape[1] > 1:
                remove_duplicated_tickers(col, data_single)
        tickers = data_single[YF_PROD_INFO_LABEL].columns.to_list()
        ticker_chosen = tickers[0] if len(tickers) == 1 else choose_ticker(tickers, ticker)
        for col in columns_ext:
            data_single[col] = data_single[col][ticker_chosen]
        # Degiro ticker if known, otherwise the Yahoo symbol, so that renaming never produces NaN labels
        data_single[YF_PROD_INFO_LABEL].loc['symbol_ext'] = ticker if ticker is not None else ticker_chosen
        for col in columns_ext:
            data_dict[col] = pd.concat([data_dict[col], data_single[col]], axis=1)
    for col in columns:
        data_dict[col].index = pd.to_datetime(data_dict[col].index)
        data_dict[col] = data_dict[col].sort_index()
        data_dict[col] = data_dict[col].resample(freq).last()
    for col in columns_ext:
        data_dict[col] = data_dict[col].loc[:, ~data_dict[col].columns.duplicated()].copy()
    return data_dict


def remove_duplicated_tickers(col, data_single):
    if col == YF_PROD_INFO_LABEL:
        data_single[col] = data_single[col].loc[:, ~data_single[col].columns.duplicated()].copy()
    else:
        data_single[col] = data_single[col].groupby(by=data_single[col].columns, axis=1).mean()


def prices_to_base_curr(
    account: Accounts, price_df: pd.DataFrame, curr_info: PD_DATA_TYPES | list[str]
) -> pd.DataFrame:
    if isinstance(curr_info, pd.Series):
        curr_lst = curr_info.str.upper().to_list()
    elif isinstance(curr_info, list):
        curr_lst = [c.upper() for c in curr_info]
    else:
        raise TypeError
    curr_foreign_lst = sorted(set([c for c in curr_lst if c != account.currency]))
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst, account=account, index=price_df.index)
    fx_rates_df = fx_rates_df.reindex(index=price_df.index).ffill()
    # convert to base currency; a ticker appearing in several columns (e.g. several exchanges) is averaged
    price_base_df = pd.DataFrame()
    for ticker, curr in zip(price_df.columns, curr_lst, strict=True):
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
        ser = ser.mul(fx_rate, axis=0).rename(ticker)
        price_base_df = pd.concat([price_base_df, ser], axis=1)
    price_base_df.index = pd.to_datetime(price_base_df.index)
    price_base_df = price_base_df.sort_index()
    return price_base_df


def fetch_portfolio_instr_adj_prices(account: Accounts) -> pd.DataFrame:
    products_df = load_portfolio_products(account=account)
    isin_lst = products_df['isin'].to_list()
    name_lst = products_df['name'].to_list()
    tick_lst = products_df['symbol'].to_list()
    close_adj_base_curr_df = fetch_instr_adj_prices(account, isin_lst, name_lst, tick_lst)
    close_adj_base_curr_df = close_adj_base_curr_df.dropna(how='all')
    return close_adj_base_curr_df


def fetch_instr_adj_prices(
    account: Accounts,
    isin_lst: list,
    name_lst: list | None = None,
    tick_lst: list | None = None,
) -> pd.DataFrame:
    data = fetch_instr_hist_data(
        isin_lst=isin_lst, columns=YFinHistCols.adj_close, ticker_lst=tick_lst, name_lst=name_lst
    )
    # convert prices to domestic currency
    close_adj_base_curr_df = prices_to_base_curr(
        account=account,
        price_df=data[YFinHistCols.adj_close],
        curr_info=data[YF_PROD_INFO_LABEL].loc[YFinInfoCols.currency.value],
    )
    rename_dict = {
        old: new
        for (old, new) in zip(
            data[YF_PROD_INFO_LABEL].loc['symbol'], data[YF_PROD_INFO_LABEL].loc['symbol_ext'], strict=True
        )
    }
    close_adj_base_curr_df = close_adj_base_curr_df.rename(columns=rename_dict)
    return close_adj_base_curr_df


def compute_product_performance(
    adj_close_df: PD_DATA_TYPES, volume_df: PD_DATA_TYPES | None = None, prod_info_df: PD_DATA_TYPES | None = None
) -> pd.DataFrame:
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
    # ticker and name from the Degiro catalog (if listed) help picking the right Yahoo Finance listing
    prod_df = query_products(product_isin=isin, product_type=ProductTypes.ETF)
    ticker = prod_df[Product.symbol.name].iloc[0] if not prod_df.empty else None
    name = prod_df[Product.name.name].iloc[0] if not prod_df.empty else None
    data = fetch_instr_hist_data(isin_lst=isin, columns=YFinHistCols.adj_close, ticker_lst=ticker, name_lst=name)
    df = compute_product_performance(adj_close_df=data[YFinHistCols.adj_close], prod_info_df=data[YF_PROD_INFO_LABEL])
    logger.debug('Performance of %s:\n%s', isin, df)
    return df


def compute_portfolio_instruments_performance():
    for account in Accounts:
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


def fetch_etf_catalog_data():
    etf_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)[
        [Product.isin.name, Product.symbol.name, Product.name.name, Product.exchange_id.name]
    ]
    exchange_ids = [x.value for x in [Exchanges.XET, Exchanges.SWX, Exchanges.MIL, Exchanges.EAM]]
    etf_info_df = etf_info_df[etf_info_df[Product.exchange_id.name].isin(exchange_ids)]
    etf_info_df = etf_info_df.drop_duplicates(subset=['isin', 'symbol'], keep='first')
    # etf_info_df = etf_info_df.iloc[:, :]
    isin_lst = etf_info_df[Product.isin.name].to_list()
    ticker_lst = etf_info_df[Product.symbol.name].to_list()
    name_lst = etf_info_df[Product.name.name].to_list()
    for n, (isin, ticker, name) in enumerate(zip(isin_lst, ticker_lst, name_lst, strict=True)):
        logger.info('(%d/%d) Fetching data for %s', n + 1, len(isin_lst), isin)
        for _attempt in range(5):
            try:
                fetch_instr_hist_data(
                    isin_lst=isin,
                    columns=[YFinHistCols.adj_close, YFinHistCols.close, YFinHistCols.volume],
                    ticker_lst=ticker,
                    name_lst=name,
                )
                break
            except ConnectionError:
                time.sleep(0.5)
                continue
        time.sleep(0.5)


def build_etf_catalog_data(account: Accounts) -> dict[YFinHistCols, pd.DataFrame]:
    volume_df = query_yahoo_finance_hist_data(columns=YFinHistCols.volume)
    volume_3m_df = volume_df.rolling(90).mean().dropna(how='all', axis=1)
    curr_info = query_yahoo_finance_prod_info().loc[YFinInfoCols.currency.value, volume_3m_df.columns]
    volume_3m_base_df = prices_to_base_curr(account=account, price_df=volume_3m_df, curr_info=curr_info)
    volume_3m_base = volume_3m_base_df.apply(lambda x: x[x.notnull()].values[-1])
    most_liquid_3m = volume_3m_base.sort_values(ascending=False).index[:MAX_ETF_CATALOG_SIZE].to_list()
    close_adj_df = query_yahoo_finance_hist_data(columns=YFinHistCols.adj_close, tickers=list(most_liquid_3m))
    close_adj_df = prices_to_base_curr(account=account, price_df=close_adj_df, curr_info=curr_info)
    most_liquid_3m = [e for e in most_liquid_3m if e in close_adj_df.columns]
    info_df = query_yahoo_finance_prod_info(ticker=list(most_liquid_3m))
    data_dict = {
        YFinHistCols.adj_close: close_adj_df[most_liquid_3m],
        YFinHistCols.volume: volume_3m_base_df[most_liquid_3m],
        YF_PROD_INFO_LABEL: info_df[most_liquid_3m],
    }
    data_dict[CATALOG_PERF_LABEL] = compute_catalog_performance_df(data_dict)
    save_etf_catalog_data(account=account, data_dict=data_dict)
    return data_dict


def compute_catalog_performance_df(data_dict: dict[str | YFinHistCols, pd.DataFrame]) -> pd.DataFrame:
    return compute_product_performance(
        adj_close_df=data_dict[YFinHistCols.adj_close],
        volume_df=data_dict[YFinHistCols.volume],
        prod_info_df=data_dict[YF_PROD_INFO_LABEL],
    )


def compute_catalog_performance(account: Accounts) -> pd.DataFrame:
    # add the performance metrics to an existing catalog, from its saved data only
    data_dict = load_etf_catalog_data(account=account)
    data_dict[CATALOG_PERF_LABEL] = compute_catalog_performance_df(data_dict)
    save_etf_catalog_data(account=account, data_dict=data_dict)
    return data_dict[CATALOG_PERF_LABEL]


def save_etf_catalog_data(account: Accounts, data_dict: dict[str | YFinHistCols, pd.DataFrame]):
    data_dict_renamed = {str(k): data_dict[k] for k in data_dict}
    save_df_dict_to_excel(df_dict=data_dict_renamed, folder_name=RESULTS_DIR, file_name=f'{account.name}_catalog')


def load_etf_catalog_data(account: Accounts) -> dict[str | YFinHistCols, pd.DataFrame]:
    # keys: YFinHistCols for the price and volume sheets, YF_PROD_INFO_LABEL, and CATALOG_PERF_LABEL if computed
    data_dict = load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=f'{account.name}_catalog')
    data_dict_renamed = {YFinHistCols.get_entry_by_val(k): data_dict[k] for k in data_dict}
    return data_dict_renamed
