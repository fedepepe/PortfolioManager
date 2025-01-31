import time
import warnings
from enum import Enum
from typing import List, Dict, Optional

import pandas as pd

import yfinance_api as yf
from charts import load_portfolio_products, load_fx_rates
from definitions import PORTFOLIO_NAME, BASE_CURRENCY, RESULTS_DIR, DATA_ETF_DIR
from file_utils import save_df_dict_to_excel, save_df_to_excel, load_df_from_excel
from product_definitions import ProductTypes, Exchanges
from reporting import compute_portfolio_metrics, OutDataTabs
from sql import query_products, query_tradable_products


def get_instruments_adj_prices(isin_lst: List[str]) -> pd.DataFrame:
	close_adj_df = pd.DataFrame()
	for isin in isin_lst:
		df = yf.search_get_history(isin=isin)
		close_adj_df = pd.concat([close_adj_df, df], axis=1)
	close_adj_df.index = pd.to_datetime(close_adj_df.index)
	close_adj_df = close_adj_df.sort_index()
	close_adj_df = close_adj_df.resample('B').last()
	# convert to base currency
	product_symbols = [s.split("__")[0].split('.')[0] for s in close_adj_df.columns]
	product_curr = [s.split("__")[1].upper() for s in close_adj_df.columns]
	curr_foreign_lst = [c for c in list(set(product_curr)) if c not in [BASE_CURRENCY]]
	fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst, index=close_adj_df.index)
	fx_rates_df = fx_rates_df.resample('B').last().reindex(index=close_adj_df.index).ffill()
	# average prices across exchanges
	close_adj_base_cur_df = pd.DataFrame()
	for symbol, cur, old_symbol in zip(product_symbols, product_curr, close_adj_df.columns.to_list()):
		try:
			ser = close_adj_df[old_symbol].mul(fx_rates_df.loc[close_adj_df.index, f'{cur}/{BASE_CURRENCY}'], axis=0)
			if isinstance(ser, pd.DataFrame):
				ser = ser.mean(axis=1)
		except KeyError:
			warnings.warn(f'Warning! Missing foreign exchange historical time series for '
			              f'{cur}/{BASE_CURRENCY}')
			continue
		close_adj_base_cur_df = pd.concat([close_adj_base_cur_df, ser.rename(symbol)], axis=1)
	close_adj_base_cur_df.index = pd.to_datetime(close_adj_base_cur_df.index)
	close_adj_base_cur_df = close_adj_base_cur_df.sort_index()
	return close_adj_base_cur_df


def get_portfolio_instruments_adj_prices() -> pd.DataFrame:
	products_df = load_portfolio_products()
	isin_lst = products_df['isin'].to_list()
	close_adj_df = get_instruments_adj_prices(isin_lst=isin_lst)
	return close_adj_df


def compute_product_performance(isin: str,
                                etf_info_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
	perf_metrics_df = pd.DataFrame()
	try:
		df = load_df_from_excel(file_name=f'{isin}_adj_close', folder=DATA_ETF_DIR)
	except FileNotFoundError:
		warnings.warn(f'Data not found for instrument {isin}.')
		return perf_metrics_df
	for ticker in df.columns:
		print(f'Loading data for {ticker} | {isin}... ')
		# compute performance metrics
		try:
			results_dict = compute_portfolio_metrics(nav=df[ticker], compute_hist_metrics=False)
		except:
			continue
		results_dict[OutDataTabs.RISK_METRICS]['isin'] = isin
		if etf_info_df is not None:
			match_name = etf_info_df.loc[(etf_info_df['isin'] == isin) &
			                             (etf_info_df['symbol'].str.startswith(ticker[:3], na=False)), 'name']
			if not match_name.empty:
				results_dict[OutDataTabs.RISK_METRICS]['name'] = match_name.iloc[0]
		perf_metrics_df = pd.concat([perf_metrics_df, results_dict[OutDataTabs.RISK_METRICS]], axis=1)
	return perf_metrics_df


class UnitTests(Enum):
	COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE = 1
	FETCH_ETF_CATALOG_ADJ_CLOSE = 2
	COMPUTE_ETF_CATALOG_PERFORMANCE = 3
	COMPUTE_SINGLE_ETF_PERFORMANCE = 4


def run_unit_test(unit_test: UnitTests):
	if unit_test == UnitTests.COMPUTE_PORTFOLIO_INSTRUMENTS_PERFORMANCE:
		close_adj_df = get_portfolio_instruments_adj_prices()
		perf_metrics_df = pd.DataFrame()
		for instr in close_adj_df.columns:
			results_dict = compute_portfolio_metrics(nav=close_adj_df[instr])
			perf_metrics_df = pd.concat([perf_metrics_df, results_dict[OutDataTabs.RISK_METRICS]], axis=1)
		save_df_dict_to_excel(df_dict={OutDataTabs.RISK_METRICS: perf_metrics_df,
		                               OutDataTabs.PRICES: close_adj_df},
		                      folder=RESULTS_DIR,
		                      file_name=f'{PORTFOLIO_NAME}_instr')
	elif unit_test == UnitTests.FETCH_ETF_CATALOG_ADJ_CLOSE:
		etf_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)
		isin_lst = list(set([e for e in etf_info_df['isin'].to_list() if e is not None]))
		for n, isin in isin_lst:
			print(f'({n}/{len(isin_lst)} - Fetching data for {isin}')
			close_adj_df = get_instruments_adj_prices(isin_lst=[isin])
			save_df_to_excel(df=close_adj_df,
			                 folder=DATA_ETF_DIR,
			                 file_name=f'{isin}_adj_close')
			time.sleep(0.5)
	elif unit_test == UnitTests.COMPUTE_ETF_CATALOG_PERFORMANCE:
		etf_info_df = query_tradable_products(product_type=ProductTypes.ETF)
		isin_lst = list(set([e for e in etf_info_df['isin'].to_list() if e is not None]))
		# aggregate adjusted closing prices
		perf_metrics_df = pd.DataFrame()
		# TODO: add traded volume from Degiro
		for n, isin in enumerate(isin_lst):
			print(f'{n + 1}/{len(isin_lst)} - ', end='')
			df = compute_product_performance(isin=isin, etf_info_df=etf_info_df.loc[etf_info_df['isin'] == isin, :])
			perf_metrics_df = pd.concat([perf_metrics_df, df], axis=1)
		save_df_to_excel(df=perf_metrics_df.T,
		                 folder=RESULTS_DIR,
		                 file_name='ETF_performance')
	elif unit_test == UnitTests.COMPUTE_SINGLE_ETF_PERFORMANCE:
		isin = 'IE00077FRP95'
		etf_info_df = query_tradable_products(product_type=ProductTypes.ETF)
		df = compute_product_performance(isin=isin, etf_info_df=etf_info_df.loc[etf_info_df['isin'] == isin, :])
		print(df)
	else:
		raise NotImplementedError


if __name__ == '__main__':
	unit_test = UnitTests.COMPUTE_ETF_CATALOG_PERFORMANCE
	run_unit_test(unit_test=unit_test)
