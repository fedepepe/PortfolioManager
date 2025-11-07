import time
import warnings
from difflib import SequenceMatcher
from enum import Enum
from typing import List, Dict, Optional

import numpy as np
import pandas as pd

from charts import load_portfolio_products, load_fx_rates
from definitions import Accounts, DEFAULT_DATA_FREQ
from definitions import RESULTS_DIR
from file_utils import PD_DATA_TYPES
from file_utils import save_df_dict_to_excel, load_df_from_excel, load_df_dict_from_excel
from product_definitions import ProductTypes
from reporting import compute_portfolio_metrics, OutDataTabs
from database.sql import query_products, query_tradable_products, insert_yahoo_finance_data
from database.sql import query_yahoo_finance_prod_info, query_yahoo_finance_hist_data
from product_definitions import Exchanges
from database.table_definitions import Product
from yfinance_api import YFinHistCols, YFinInfoCols, search_fetch_history, YF_PROD_INFO_LABEL
from yfinance_api import Exchanges as ExchangesYF

MAX_ETF_CATALOG_SIZE = 250


class InstrPerfTableCols:
    ticker = 'Ticker'
    isin = 'ISIN'
    name = 'Name'
    volume = 'Volume'


def fetch_instr_hist_data(isin_lst: str | List[str],
                          columns: str | YFinHistCols | List[str] | List[YFinHistCols],
                          ticker_lst: Optional[str | List[str]] = None,
                          name_lst: Optional[str | List[str]] = None,
                          freq: str = DEFAULT_DATA_FREQ,
                          to_portfolio_instr_table: Optional[bool] = False,
                          ) -> Dict[str | YFinHistCols, pd.DataFrame | pd.Series]:
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
        if ticker_lst[n] is None:
            continue
        # 1. try with isin
        data_single = search_fetch_history(isin=isin, columns=columns)
        # 2. if no results is given, try with ticker
        if all([data_single[col].empty for col in columns]):
            if ticker_lst[n] is not None:
                data_single = search_fetch_history(ticker=ticker_lst[n], columns=columns)
            else:
                continue
        # 3. if still no results is given, give up
        if data_single is None or all([df.empty for df in data_single.values()]):
            continue
        # 4. restrict to tickers that appear in all dataframes
        tickers_restrict = list(set.intersection(*map(set, [data_single[key].columns for key in data_single])))
        for key in data_single:
            data_single[key] = data_single[key].loc[:, tickers_restrict]
        # 5. restrict to tickers with name matching
        if len(tickers_restrict) > 1 and name_lst[n] is not None:
            names_long = data_single[YF_PROD_INFO_LABEL].loc[YFinInfoCols.name_long.value].values
            name_match = [SequenceMatcher(None, name_lst[n], name).ratio() for name in names_long]
            ticker_match = data_single[YF_PROD_INFO_LABEL].columns[name_match.index(max(name_match))]
            for key in data_single:
                data_single[key] = data_single[key].loc[:, [ticker_match]]
        # at this point, just choose the ticker according to priority arbitrarily assigned to exchanges
        if all([df.empty for df in data_single.values()]):
            continue
        for col in columns_ext:
            if data_single[col].shape[1] == 1:
                data_single[col] = data_single[col].iloc[:, 0]
            elif data_single[col].shape[1] > 1:
                remove_duplicated_tickers(col, data_single)
                if data_single[col].shape[1] == 1:
                    data_single[col] = data_single[col].iloc[:, 0]
                else:
                    is_in_x_dct = {f'{ticker_lst[n]}.{x.code}': f'{ticker_lst[n]}.{x.code}' in data_single[col].columns
                                   for x in ExchangesYF}
                    data_single[col] = data_single[col].loc[:, max(is_in_x_dct, key=is_in_x_dct.get)]
        if ticker_lst[n] not in [None, np.nan]:
            data_single[YF_PROD_INFO_LABEL].loc['symbol_ext'] = ticker_lst[n]
        for col in columns_ext:
            data_dict[col] = pd.concat([data_dict[col], data_single[col]], axis=1)
    for col in columns:
        data_dict[col].index = pd.to_datetime(data_dict[col].index)
        data_dict[col] = data_dict[col].sort_index()
        data_dict[col] = data_dict[col].resample(freq).last()
    for col in columns_ext:
        data_dict[col] = data_dict[col].loc[:, ~data_dict[col].columns.duplicated()].copy()
    # dump collected data into the database
    if to_portfolio_instr_table is not None:
        insert_yahoo_finance_data(data_dict=data_dict, to_portfolio_instr_table=to_portfolio_instr_table)
    return data_dict


def remove_duplicated_tickers(col, data_single):
    if col == YF_PROD_INFO_LABEL:
        data_single[col] = data_single[col].loc[:, ~data_single[col].columns.duplicated()].copy()
    else:
        data_single[col] = data_single[col].groupby(by=data_single[col].columns, axis=1).mean()


def prices_to_base_curr(account: Accounts,
                        price_df: pd.DataFrame,
                        curr_info: PD_DATA_TYPES | List[str]):
    if isinstance(curr_info, pd.Series):
        curr_lst = curr_info.str.upper().to_list()
    elif isinstance(curr_info, List):
        curr_lst = [c.upper() for c in curr_info]
    else:
        raise TypeError
    curr_foreign_lst = list(set([c for c in curr_lst if c != account.currency]))
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst,
                                account=account,
                                index=price_df.index)
    fx_rates_df = fx_rates_df.reindex(index=price_df.index).ffill()
    # average prices across exchanges
    price_base_df = pd.DataFrame()
    for ticker, curr in zip(price_df.columns, curr_lst):
        try:
            ser = (price_df[ticker].mul(fx_rates_df.loc[price_df.index, f'{curr}/{account.currency}'], axis=0))
            ser = ser.rename(ticker)
            if isinstance(ser, pd.DataFrame):
                ser = ser.mean(axis=1)
                breakpoint()
        except KeyError:
            warnings.warn(f'Warning! Missing foreign exchange historical time series for {curr}/{account.currency}')
            continue
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
    return close_adj_base_curr_df


def fetch_instr_adj_prices(account: Accounts,
                           isin_lst: List,
                           name_lst: Optional[List] = None,
                           tick_lst: Optional[List] = None) -> pd.DataFrame:
    data = fetch_instr_hist_data(isin_lst=isin_lst,
                                 columns=YFinHistCols.adj_close,
                                 ticker_lst=tick_lst,
                                 name_lst=name_lst,
                                 to_portfolio_instr_table=True)
    close_adj_base_curr_df = prices_to_base_curr(account=account,
                                                 price_df=data[YFinHistCols.adj_close],
                                                 curr_info=data[YF_PROD_INFO_LABEL].loc[YFinInfoCols.currency.value])
    rename_dict = {old: new for (old, new) in zip(data[YF_PROD_INFO_LABEL].loc['symbol'],
                                                  data[YF_PROD_INFO_LABEL].loc['symbol_ext'])}
    close_adj_base_curr_df = close_adj_base_curr_df.rename(columns=rename_dict)
    return close_adj_base_curr_df


def compute_product_performance(adj_close_df: PD_DATA_TYPES,
                                volume_df: Optional[PD_DATA_TYPES] = None,
                                prod_info_df: Optional[PD_DATA_TYPES] = None) -> pd.DataFrame:
    perf_metrics_df = pd.DataFrame()
    for ticker in adj_close_df.columns:
        print(f"Computing performance metrics for {ticker} ({prod_info_df.loc['isin', ticker]})... ")
        # compute performance metrics
        try:
            results_dict = compute_portfolio_metrics(nav=adj_close_df[ticker].dropna(),
                                                     compute_hist_metrics=False,
                                                     print_results=False)
        except ValueError:
            continue
        # add dollar volume
        if volume_df is not None:
            if ticker in volume_df.columns:
                volume_mean_90 = int(volume_df[ticker][volume_df[ticker].notnull()].values[-1])
                results_dict[OutDataTabs.RISK_METRICS][InstrPerfTableCols.volume] = volume_mean_90
        if prod_info_df is not None:
            isin = prod_info_df.loc[YFinInfoCols.isin.value, ticker]
            results_dict[OutDataTabs.RISK_METRICS][InstrPerfTableCols.isin] = isin
            name = prod_info_df.loc[YFinInfoCols.name_long.value, ticker]
            results_dict[OutDataTabs.RISK_METRICS][InstrPerfTableCols.name] = name
        perf_metrics_df = pd.concat([perf_metrics_df, results_dict[OutDataTabs.RISK_METRICS]], axis=1)
    perf_metrics_df = perf_metrics_df.T.copy()
    perf_metrics_df.index.name = InstrPerfTableCols.ticker
    perf_metrics_df = perf_metrics_df.reset_index().copy()
    return perf_metrics_df


def compute_single_etf_performance(isin: str):
    etf_info_df = query_tradable_products(product_type=ProductTypes.ETF)
    df = compute_product_performance(isin=isin,
                                     prod_info_df=etf_info_df.loc[etf_info_df['isin'] == isin, :])
    print(df)


def compute_portfolio_instruments_performance():
    for account in Accounts:
        close_adj_df = fetch_portfolio_instr_adj_prices(account=account)
        perf_metrics_df = pd.DataFrame()
        for instr in close_adj_df.columns:
            results_dict = compute_portfolio_metrics(nav=close_adj_df[instr])
            perf_metrics_df = pd.concat([perf_metrics_df, results_dict[OutDataTabs.RISK_METRICS]], axis=1)
        save_df_dict_to_excel(df_dict={OutDataTabs.RISK_METRICS: perf_metrics_df,
                                       OutDataTabs.PRICES: close_adj_df},
                              folder_name=RESULTS_DIR,
                              file_name=f'{account.name}_instr')


def fetch_etf_catalog_data():
    etf_info_df = query_products(product_type=ProductTypes.ETF,
                                 tradable=True
                                 )[[Product.isin.name,
                                    Product.symbol.name,
                                    Product.name.name,
                                    Product.exchange_id.name
                                    ]]
    exchange_ids = [x.value for x in [Exchanges.XET, Exchanges.SWX, Exchanges.MIL, Exchanges.EAM]]
    etf_info_df = etf_info_df[etf_info_df[Product.exchange_id.name].isin(exchange_ids)]
    etf_info_df = etf_info_df.drop_duplicates(subset=['isin', 'symbol'], keep='first')
    # etf_info_df = etf_info_df.iloc[:, :]
    isin_lst = etf_info_df[Product.isin.name].to_list()
    ticker_lst = etf_info_df[Product.symbol.name].to_list()
    name_lst = etf_info_df[Product.name.name].to_list()
    for n, (isin, ticker, name) in enumerate(zip(isin_lst, ticker_lst, name_lst)):
        print(f'({n + 1}/{len(isin_lst)} - Fetching data for {isin}')
        for attempt in range(5):
            try:
                fetch_instr_hist_data(isin_lst=isin,
                                      columns=[YFinHistCols.adj_close,
                                               YFinHistCols.close,
                                               YFinHistCols.volume],
                                      ticker_lst=ticker,
                                      name_lst=name,
                                      to_portfolio_instr_table=False)
                break
            except ConnectionError:
                time.sleep(0.5)
                continue
        time.sleep(0.5)


def load_etf_catalog_performance() -> pd.DataFrame:
    return load_df_from_excel(file_name='ETF_performance', folder_name=RESULTS_DIR)


def build_etf_catalog_data(account: Accounts) -> Dict[YFinHistCols, pd.DataFrame]:
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
    data_dict = {YFinHistCols.adj_close: close_adj_df[most_liquid_3m],
                 YFinHistCols.volume: volume_3m_base_df[most_liquid_3m],
                 YF_PROD_INFO_LABEL: info_df[most_liquid_3m]}
    data_dict_renamed = {str(k): data_dict[k] for k in data_dict}
    save_df_dict_to_excel(df_dict=data_dict_renamed, folder_name=RESULTS_DIR, file_name=f'{account.name}_catalog')
    return data_dict


def load_etf_catalog_data(account: Accounts) -> Dict[YFinHistCols, pd.DataFrame]:
    data_dict = load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=f'{account.name}_catalog')
    data_dict_renamed = {YFinHistCols.get_entry_by_val(k): data_dict[k] for k in data_dict}
    return data_dict_renamed


class UnitTests(Enum):
    COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE = 1
    FETCH_ETF_CATALOG_DATA = 2
    COMPUTE_SINGLE_ETF_PERFORMANCE = 4
    FETCH_SINGLE_ETF_ADJ_PRICE = 5
    LOAD_ETF_CATALOG_DATA = 6
    BUILD_ETF_CATALOG_DATA = 7


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE:
        compute_portfolio_instruments_performance()
    elif unit_test == UnitTests.FETCH_ETF_CATALOG_DATA:
        fetch_etf_catalog_data()
    elif unit_test == UnitTests.COMPUTE_SINGLE_ETF_PERFORMANCE:
        compute_single_etf_performance(isin='IE00B7N3YW49')
    elif unit_test == UnitTests.FETCH_SINGLE_ETF_ADJ_PRICE:
        data = fetch_instr_hist_data(isin_lst='IE00BWC52G65',
                                     # ticker_lst='STHC.SW',
                                     columns=YFinHistCols.adj_close,
                                     to_portfolio_instr_table=None)
        print(data)
    elif unit_test == UnitTests.LOAD_ETF_CATALOG_DATA:
        df = load_etf_catalog_data(account=Accounts.CHF)
        print(df)
    elif unit_test == UnitTests.BUILD_ETF_CATALOG_DATA:
        build_etf_catalog_data(account=Accounts.CHF)
    else:
        raise NotImplementedError


if __name__ == '__main__':
    unit_test = UnitTests.FETCH_SINGLE_ETF_ADJ_PRICE
    run_unit_test(unit_test=unit_test)
