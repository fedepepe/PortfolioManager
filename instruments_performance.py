import time
import warnings
from difflib import SequenceMatcher
from enum import Enum
from typing import List, Dict, Optional

import pandas as pd

from charts import load_portfolio_products, load_fx_rates
from definitions import Accounts, DEFAULT_DATA_FREQ
from definitions import RESULTS_DIR, DATA_ETF_DIR
from file_utils import PD_DATA_TYPES
from file_utils import save_df_dict_to_excel, save_df_to_excel, load_df_from_excel
from product_definitions import ProductTypes
from reporting import compute_portfolio_metrics, OutDataTabs
from sql import query_products, query_tradable_products, insert_yahoo_finance_data
from yfinance_api import YFinHistCols, YFinProdInfo, search_fetch_history, YF_PROD_INFO_LABEL


def fetch_instr_hist_data(isin_lst: str | List[str],
                          columns: str | YFinHistCols | List[str] | List[YFinHistCols],
                          name_lst: Optional[str | List[str]] = None,
                          tickers_rename: Optional[str | List[str]] = None,
                          freq: str = DEFAULT_DATA_FREQ,
                          ) -> Dict[str, pd.DataFrame | pd.Series]:
    if isinstance(isin_lst, str):
        isin_lst = [isin_lst]
    if isinstance(name_lst, str):
        name_lst = [name_lst]
    if tickers_rename is None:
        tickers_rename = isin_lst.copy()
    elif isinstance(tickers_rename, str):
        tickers_rename = [tickers_rename]
    if isinstance(columns, str) or isinstance(columns, YFinHistCols):
        columns = [str(columns)]
    columns_ext = columns + [YF_PROD_INFO_LABEL]
    data_dict = {col: pd.DataFrame() for col in columns_ext}
    for n, (isin, ticker) in enumerate(zip(isin_lst, tickers_rename)):
        data_single = search_fetch_history(isin=isin, columns=columns)
        tickers_restrict = list(set.intersection(*map(set, [data_single[key].columns for key in data_single])))
        for key in data_single:
            data_single[key] = data_single[key].loc[:, tickers_restrict]
        for col in columns_ext:
            if data_single[col].shape[1] == 1:
                data_single[col] = data_single[col].iloc[:, 0]
            elif data_single[col].shape[1] > 1:
                raise Exception
            else:  # try with ticker
                data_single = search_fetch_history(ticker=ticker, columns=columns)
                if data_single[col].empty:
                    continue
                breakpoint()
                names_long = data_single[YF_PROD_INFO_LABEL].loc[YFinProdInfo.name_long.value].iloc[0, :].values
                name_match = [SequenceMatcher(None, name_lst[n], name).ratio() for name in names_long]
                ticker_best = data_single[YF_PROD_INFO_LABEL].loc[YFinProdInfo.name_long.value].columns[name_match.index(max(name_match))]
                for key in data_single:
                    data_single[key] = data_single[key].loc[:, ticker_best]
            data_single[col] = data_single[col].rename(ticker)
        for col in columns_ext:
            data_dict[col] = pd.concat([data_dict[col], data_single[col]], axis=1)
    for col in columns:
        data_dict[col].index = pd.to_datetime(data_dict[col].index)
        data_dict[col] = data_dict[col].sort_index()
        data_dict[col] = data_dict[col].resample(freq).last()
        data_dict[col] = data_dict[col].loc[:, ~data_dict[col].columns.duplicated()].copy()
    insert_yahoo_finance_data(data_dict=data_dict)
    return data_dict


def prices_to_base_curr(account: Accounts,
                        price_df: pd.DataFrame,
                        curr_info: PD_DATA_TYPES | List[str]):
    if isinstance(curr_info, pd.DataFrame):
        curr_lst = curr_info.loc[YFinHistCols.currency, :].str.upper().to_list()
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
    data = fetch_instr_hist_data(isin_lst=isin_lst,
                                 columns=YFinHistCols.adj_close,
                                 name_lst=name_lst,
                                 tickers_rename=products_df['symbol'].to_list())
    close_adj_base_curr_df = prices_to_base_curr(account=account,
                                                 price_df=data[YFinHistCols.adj_close],
                                                 curr_info=data[YF_PROD_INFO_LABEL].loc[YFinProdInfo.currency.value])
    return close_adj_base_curr_df


def compute_product_performance(isin: str,
                                etf_info_df: Optional[pd.DataFrame] = None,
                                use_local_data: bool = False) -> pd.DataFrame:
    perf_metrics_df = pd.DataFrame()
    if use_local_data:
        data = load_df_from_excel(folder_name=DATA_ETF_DIR,
                                  file_name=isin)
    else:
        data = fetch_instr_hist_data(isin_lst=isin,
                                     columns=YFinHistCols.adj_close)
    for ticker in data[YFinHistCols.adj_close].columns:
        print(f'Computing performance metrics for {ticker} | {isin}... ')
        # compute performance metrics
        try:
            results_dict = compute_portfolio_metrics(nav=data[YFinHistCols.adj_close][ticker],
                                                     compute_hist_metrics=False,
                                                     print_results=False)
        except ValueError:
            continue
        # add dollar volume
        try:
            volume = data[YFinHistCols.close][ticker].mul(data[YFinHistCols.volume][ticker])
            results_dict[OutDataTabs.RISK_METRICS]['Volume ($)'] = volume.rolling(60, min_periods=1).mean().iloc[-1]
        except ValueError:
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


def compute_single_etf_performance(isin: str):
    etf_info_df = query_tradable_products(product_type=ProductTypes.ETF)
    df = compute_product_performance(isin=isin,
                                     etf_info_df=etf_info_df.loc[etf_info_df['isin'] == isin, :])
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
                                      folder_name=DATA_ETF_DIR,
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
        df = compute_product_performance(isin=isin,
                                         etf_info_df=etf_info_df.loc[etf_info_df['isin'] == isin, :],
                                         use_local_data=True)
        perf_metrics_df = pd.concat([perf_metrics_df, df], axis=1)
    save_df_to_excel(df=perf_metrics_df.T,
                     folder_name=RESULTS_DIR,
                     file_name='ETF_performance')
    return perf_metrics_df


def load_etf_catalog_performance() -> pd.DataFrame:
    return load_df_from_excel(file_name='ETF_performance', folder_name=RESULTS_DIR)


def load_etf_catalog_data(account: Accounts,
                          column: str,
                          isin_lst: Optional[str | List[str]] = None) -> pd.DataFrame:
    if isin_lst is None:
        isin_lst = load_etf_catalog_performance()['ISIN'].to_list()
    if isinstance(isin_lst, str):
        isin_lst = [isin_lst]
    df = pd.DataFrame()
    curr_lst = []
    for n, isin in enumerate(isin_lst):
        data_single = load_df_from_excel(folder_name=DATA_ETF_DIR,
                                         file_name=isin,
                                         sheet_name=[column, YF_PROD_INFO_LABEL])
        if data_single[column].empty:
            continue
        df = pd.concat([df, data_single[column]], axis=1)
        curr_lst.append(data_single[YF_PROD_INFO_LABEL].loc[YFinProdInfo.currency.value])
        print(f'{n}/{len(isin_lst)} loaded.')
        if n > 50:
            break
    df = df.loc[:, ~df.columns.duplicated()].copy()
    df = prices_to_base_curr(account=account, price_df=df, curr_info=curr_lst)
    df = df.sort_index()
    return df


class UnitTests(Enum):
    COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE = 1
    FETCH_ETF_CATALOG_DATA = 2
    COMPUTE_ETF_CATALOG_PERFORMANCE = 3
    COMPUTE_SINGLE_ETF_PERFORMANCE = 4
    FETCH_SINGLE_ETF_ADJ_PRICE = 5
    LOAD_ETF_CATALOG_DATA = 6


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE:
        compute_portfolio_instruments_performance()
    elif unit_test == UnitTests.FETCH_ETF_CATALOG_DATA:
        fetch_etf_catalog_data()
    elif unit_test == UnitTests.COMPUTE_ETF_CATALOG_PERFORMANCE:
        compute_etf_catalog_performance()
    elif unit_test == UnitTests.COMPUTE_SINGLE_ETF_PERFORMANCE:
        compute_single_etf_performance(isin='IE00B7N3YW49')
    elif unit_test == UnitTests.FETCH_SINGLE_ETF_ADJ_PRICE:
        data = fetch_instr_hist_data(isin_lst='IE00B7N3YW49', columns=YFinHistCols.adj_close)
        print(data)
    elif unit_test == UnitTests.LOAD_ETF_CATALOG_DATA:
        df = load_etf_catalog_data(column=YFinHistCols.adj_close)
        print(df)
    else:
        raise NotImplementedError


if __name__ == '__main__':
    unit_test = UnitTests.FETCH_SINGLE_ETF_ADJ_PRICE
    run_unit_test(unit_test=unit_test)
