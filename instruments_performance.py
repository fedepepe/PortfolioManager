import time
import warnings
from enum import Enum
from typing import List, Dict, Optional

import pandas as pd

from charts import load_portfolio_products, load_fx_rates
from definitions import Accounts, DEFAULT_DATA_FREQ
from definitions import RESULTS_DIR, DATA_ETF_DIR
from file_utils import save_df_dict_to_excel, save_df_to_excel, load_df_dict_from_excel
from product_definitions import Currencies
from product_definitions import ProductTypes
from reporting import compute_portfolio_metrics, OutDataTabs
from sql import query_products, query_tradable_products
from yfinance_api import YFinHistCols, search_fetch_history


def fetch_instr_hist_data(isin_lst: List[str],
                          columns: str | YFinHistCols | List[str] | List[YFinHistCols],
                          tickers_rename: Optional[List[str]] = None,
                          freq: str = DEFAULT_DATA_FREQ,
                          ) -> Dict[str, pd.DataFrame | pd.Series]:
    if tickers_rename is None:
        tickers_rename = isin_lst.copy()
    if isinstance(columns, str) or isinstance(columns, YFinHistCols):
        columns = [columns]
    columns_ext = columns + [YFinHistCols.currency]
    data = {col: pd.DataFrame() for col in columns_ext}
    for isin, ticker in zip(isin_lst, tickers_rename):
        data_isin = search_fetch_history(isin=isin, columns=columns)
        for col in columns:
            try:
                data_isin[col] = data_isin[col].iloc[:, 0]
            except IndexError:
                continue
            data_isin[col] = data_isin[col].rename(ticker)
        for col in columns_ext:
            data[col] = pd.concat([data[col], data_isin[col]], axis=1)
    for col in columns:
        data[col].index = pd.to_datetime(data[col].index)
        data[col] = data[col].sort_index()
        data[col] = data[col].resample(freq).last()
        data[col] = data[col].loc[:, ~data[col].columns.duplicated()].copy()
    return data


def prices_to_curr_dom(account: Accounts,
                       price_df: pd.DataFrame,
                       curr_info: pd.Series,
                       freq: str = DEFAULT_DATA_FREQ):
    product_curr = curr_info.loc[YFinHistCols.currency, :].to_list()
    curr_foreign_lst = [c for c in list(set(product_curr)) if c != account.currency]
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst,
                                account=account,
                                index=price_df.index)
    fx_rates_df = fx_rates_df.resample(freq).last().reindex(index=price_df.index).ffill()
    # average prices across exchanges
    price_base_df = pd.DataFrame()
    for ticker, curr in zip(price_df.columns, product_curr):
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
    data = fetch_instr_hist_data(isin_lst=isin_lst,
                                 columns=YFinHistCols.adj_close,
                                 tickers_rename=products_df['symbol'].to_list())
    close_adj_base_curr_df = prices_to_curr_dom(account=account,
                                                price_df=data[YFinHistCols.adj_close],
                                                curr_info=data[YFinHistCols.currency])
    return close_adj_base_curr_df


def compute_product_performance(isin: str,
                                etf_info_df: Optional[pd.DataFrame] = None,
                                curr_dom: Optional[Currencies] = None) -> pd.DataFrame:
    perf_metrics_df = pd.DataFrame()
    try:
        data = load_df_dict_from_excel(file_name=isin, folder=DATA_ETF_DIR)
    except FileNotFoundError:
        warnings.warn(f'Data not found for instrument {isin}.')
        return perf_metrics_df
    for ticker in data[YFinHistCols.adj_close].columns:
        print(f'Computing performance metrics for {ticker} | {isin}... ')
        # compute performance metrics
        try:
            results_dict = compute_portfolio_metrics(nav=data[YFinHistCols.adj_close][ticker],
                                                     compute_hist_metrics=False,
                                                     print_results=False)
        except:
            continue
        # add dollar volume
        try:
            volume = data[YFinHistCols.close][ticker].mul(data[YFinHistCols.volume][ticker])
            results_dict[OutDataTabs.RISK_METRICS]['Volume ($)'] = volume.rolling(60, min_periods=1).mean().iloc[-1]
        except:
            pass
        # add name
        results_dict[OutDataTabs.RISK_METRICS]['ISIN'] = isin
        if etf_info_df is not None:
            match_name = etf_info_df.loc[(etf_info_df['isin'] == isin) &
                                         (etf_info_df['symbol'].str.startswith(ticker[:3], na=False)), 'name']
            if not match_name.empty:
                results_dict[OutDataTabs.RISK_METRICS]['Name'] = match_name.iloc[0]
        perf_metrics_df = pd.concat([perf_metrics_df, results_dict[OutDataTabs.RISK_METRICS]], axis=1)
    return perf_metrics_df


def compute_single_etf_performance(isin: str, curr_dom: Optional[Currencies] = None):
    etf_info_df = query_tradable_products(product_type=ProductTypes.ETF)
    df = compute_product_performance(isin=isin,
                                     etf_info_df=etf_info_df.loc[etf_info_df['isin'] == isin, :],
                                     curr_dom=curr_dom)
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
                              folder=RESULTS_DIR,
                              file_name=f'{account.name}_instr')


def fetch_etf_catalog_data():
    etf_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)
    isin_lst = list(set([e for e in etf_info_df['isin'].to_list() if e is not None]))
    for n, isin in enumerate(isin_lst):
        print(f'({n + 1}/{len(isin_lst)} - Fetching data for {isin}')
        for attempt in range(5):
            try:
                data = fetch_instr_hist_data(isin_lst=[isin], columns=[YFinHistCols.adj_close,
                                                                       YFinHistCols.close,
                                                                       YFinHistCols.volume])
                save_df_dict_to_excel(df_dict=data,
                                      folder=DATA_ETF_DIR,
                                      file_name=isin)
                break
            except ConnectionError:
                time.sleep(0.5)
                continue
        time.sleep(0.5)


def compute_etf_catalog_performance() -> pd.DataFrame:
    etf_info_df = query_tradable_products(product_type=ProductTypes.ETF)
    isin_lst = list(set([e for e in etf_info_df['isin'].to_list() if e is not None]))
    # aggregate adjusted closing prices
    perf_metrics_df = pd.DataFrame()
    for n, isin in enumerate(isin_lst):
        print(f'{n + 1}/{len(isin_lst)} - ', end='')
        df = compute_product_performance(isin=isin, etf_info_df=etf_info_df.loc[etf_info_df['isin'] == isin, :])
        perf_metrics_df = pd.concat([perf_metrics_df, df], axis=1)
    save_df_to_excel(df=perf_metrics_df.T,
                     folder=RESULTS_DIR,
                     file_name='ETF_performance')
    return perf_metrics_df


class UnitTests(Enum):
    COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE = 1
    FETCH_ETF_CATALOG_DATA = 2
    COMPUTE_ETF_CATALOG_PERFORMANCE = 3
    COMPUTE_SINGLE_ETF_PERFORMANCE = 4


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE:
        compute_portfolio_instruments_performance()
    elif unit_test == UnitTests.FETCH_ETF_CATALOG_DATA:
        fetch_etf_catalog_data()
    elif unit_test == UnitTests.COMPUTE_ETF_CATALOG_PERFORMANCE:
        compute_etf_catalog_performance()
    elif unit_test == UnitTests.COMPUTE_SINGLE_ETF_PERFORMANCE:
        compute_single_etf_performance(isin='IE00B7N3YW49')
    else:
        raise NotImplementedError


if __name__ == '__main__':
    unit_test = UnitTests.COMPUTE_SINGLE_ETF_PERFORMANCE
    run_unit_test(unit_test=unit_test)
