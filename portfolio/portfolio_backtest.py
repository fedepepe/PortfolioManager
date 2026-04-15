import re
from dataclasses import asdict
from datetime import datetime
from enum import Enum

import pandas as pd

from config.accounts import Accounts
from config.definitions import DATA_DIR, DEFAULT_DATA_FREQ
from degiro.charts import fetch_portfolio_charts, fetch_fx_charts, load_portfolio_charts, load_fx_rates
from degiro.degiro_connection import get_degiro_connection
from degiro.products import fetch_portfolio_products_info, load_portfolio_products
from degiro.transactions import fetch_tx_history, fetch_account_movements, load_tx_history, load_account_movements, \
	TxHistFields
from engines.portfolio_optimization import compute_weights_optim_portfolio
from engines.reporting import compute_results_from_navs
from portfolio.instruments_performance import fetch_portfolio_instr_adj_prices, fetch_instr_adj_prices
from portfolio.portfolio import PortfolioBacktestData
from portfolio.portfolio_backtest_engine import backtest_portfolio
from portfolio.portfolio_performance import save_performance_data
from strategy.strategy_definitions import AllocationStrats
from utils import file_utils as fu
from utils.date_utils import reset_time


def save_backtest_data(hist_portfolio_data: PortfolioBacktestData):
	fu.save_df_dict_to_excel(df_dict=asdict(hist_portfolio_data),
	                         file_name=hist_portfolio_data.name,
	                         folder_name=DATA_DIR)


def load_backtest_data(account: Accounts) -> PortfolioBacktestData:
	data_dict = fu.load_df_dict_from_excel(file_name=account.name, folder_name=DATA_DIR)
	data_dict['name'] = account.name
	return PortfolioBacktestData(**data_dict)


def update_data(account: Accounts):
	conn = get_degiro_connection(file_name=account.config_file)
	fetch_tx_history(account=account, degiro_conn=conn)
	fetch_portfolio_products_info(account=account, degiro_conn=conn)
	fetch_account_movements(account=account, degiro_conn=conn)
	fetch_portfolio_charts(account=account, degiro_conn=conn)
	fetch_fx_charts(account=account, degiro_conn=conn)
	account.state.set('last_data_update', datetime.now().strftime('%d%b%Y'))


def backtest_portfolio_account(account: Accounts) -> PortfolioBacktestData:
	# prices
	prices_df = load_portfolio_charts(account=account)
	# transaction history
	tx_hist_df = load_tx_history(account=account)
	# account movements
	account_mvmts_df = load_account_movements(account=account)
	# dividends
	dividends_df = account_mvmts_df.loc[account_mvmts_df['description'].str.contains("Dividendo|Cedola"), :].copy()
	# deposits/withdrawals
	account_mvmts_df = account_mvmts_df.loc[~account_mvmts_df['description'].str.contains('Credito FX|Prelievo FX'), :]
	deposits_df = account_mvmts_df.loc[account_mvmts_df['description'].str.contains('Deposito|Prelievo'), :].copy()
	# forex rates
	products_df = load_portfolio_products(account=account)
	curr_foreign_lst = list(set(products_df['currency'].to_list() + dividends_df['currency'].to_list()))
	curr_foreign_lst = [c for c in curr_foreign_lst if c != account.currency]
	fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst,
	                            account=account,
	                            index=prices_df.index)

	product_ids = list(set(tx_hist_df[TxHistFields.product_id].to_list()))
	product_curr = products_df.loc[product_ids, 'currency'].to_list()
	tx_hist_df = tx_hist_df.dropna(subset=['order_type_id'])

	# compute initial cash balance
	initial_cash_balance = deposits_df.loc[deposits_df.index <= tx_hist_df.index[0], 'change'].sum()

	# splits and product changes
	splits_df = account_mvmts_df[account_mvmts_df['description'].str.contains('FRAZIONAMENTO')].copy()
	splits_df['mult'] = [int(re.search(r'\d+', s).group()) for s in splits_df['description']]
	splits_df['mult'] = splits_df['mult'].where(splits_df['change'] < 0, 1. / splits_df['mult'])
	splits_df = splits_df.groupby([splits_df.index.name, 'product_id'])['mult'].prod().reset_index(level=1)

	def adjust_tx_for_split(tx_hist_df: pd.DataFrame, splits_df: pd.DataFrame):
		for ts, prod_id, mult in zip(splits_df.index, splits_df['product_id'].to_list(), splits_df['mult'].to_list()):
			bool_mask = (tx_hist_df.index <= ts) & (tx_hist_df['product_id'] == prod_id)
			tx_hist_df.loc[bool_mask, TxHistFields.quantity] = tx_hist_df.loc[bool_mask, TxHistFields.quantity].mul(mult)
			tx_hist_df.loc[bool_mask, TxHistFields.price] = tx_hist_df.loc[bool_mask, TxHistFields.price].div(mult)

	adjust_tx_for_split(tx_hist_df=tx_hist_df, splits_df=splits_df)
	prod_change_df = account_mvmts_df[account_mvmts_df['description'].str.contains('CAMBIO')].copy()
	prod_change_df = prod_change_df.set_index('value_date')
	prod_change_df['product'] = [re.search(r'\d+(.*?)@', s).group(1) for s in prod_change_df['description']]

	def product_change_fun(x):
		prod_id_old = x.loc[x['description'].str.contains('Vendita'), 'product_id'].iloc[0]
		prod_id_new = x.loc[x['description'].str.contains('Acquisto'), 'product_id'].iloc[0]
		return pd.DataFrame(prod_id_new, index=[prod_id_old], columns=['product_id'])

	prod_change_df = prod_change_df.groupby([prod_change_df.index.name, 'product']).apply(product_change_fun)
	if not prod_change_df.empty:
		prod_change_dct = prod_change_df.reset_index(level=[0, 1])[TxHistFields.product_id].to_dict()
	else:
		prod_change_dct = {}

	for id_old, id_new in prod_change_dct.items():
		tx_hist_df[TxHistFields.product_id] = tx_hist_df[TxHistFields.product_id].replace(id_old, id_new)
		dividends_df[TxHistFields.product_id] = dividends_df[TxHistFields.product_id].replace(id_old, id_new)
	adjust_tx_for_split(tx_hist_df=tx_hist_df, splits_df=splits_df)

	# reset datetime and restrict to a suitable timeframe
	tx_hist_df[TxHistFields.symbol] = products_df.loc[tx_hist_df[TxHistFields.product_id], 'symbol'].to_list()
	tx_hist_df[TxHistFields.symbol] = tx_hist_df[TxHistFields.symbol].fillna(tx_hist_df.product_id)
	tx_hist_df.loc[:, 'Date'] = [reset_time(ts) for ts in tx_hist_df.index]
	dividends_df[TxHistFields.symbol] = products_df.loc[dividends_df[TxHistFields.product_id], 'symbol'].to_list()
	dividends_df[TxHistFields.symbol] = dividends_df[TxHistFields.symbol].fillna(dividends_df.product_id)
	dividends_df.loc[:, 'Date'] = [reset_time(ts) for ts in dividends_df['value_date']]
	deposits_df.loc[:, 'Date'] = [reset_time(ts) for ts in deposits_df['value_date']]
	ts_start = tx_hist_df['Date'].iloc[0] - pd.tseries.offsets.BDay(1)
	prices_df = prices_df.loc[prices_df.index >= ts_start, product_ids].ffill()
	fx_rates_df = fx_rates_df.loc[fx_rates_df.index >= ts_start, :].reindex(prices_df.index).ffill()
	deposits_df = deposits_df.loc[deposits_df.index >= ts_start, :]

	# check all dividend dates are in the price datetime index
	assert all([d in prices_df.index for d in dividends_df['Date']])
	assert all([d in prices_df.index for d in deposits_df['Date']])

	# currency conversion to base currency
	for prod_id, curr in zip(product_ids, product_curr):
		prices_df.loc[:, prod_id] = prices_df[prod_id].mul(
			fx_rates_df.loc[prices_df.index, f'{curr}/{account.currency}'])
	fx_rates = [fx_rates_df.loc[dividends_df.iloc[n]['Date'], f'{curr}/{account.currency}']
	            for n, curr in enumerate(dividends_df['currency'])]
	dividends_df.loc[:, 'fx_rate'] = fx_rates
	dividends_df.loc[:, 'amount_base_currency'] = dividends_df['change'].mul(dividends_df['fx_rate'])

	# adjusted closing prices from Yahoo Finance
	close_adj_df = fetch_portfolio_instr_adj_prices(account=account)

	# compute historical portfolio data
	backtest_data = backtest_portfolio(account=account,
	                                   prices_df=prices_df,
	                                   tx_hist_df=tx_hist_df,
	                                   curr_base=account.currency,
	                                   initial_cash_balance=initial_cash_balance,
	                                   div_hist_df=dividends_df,
	                                   fx_rates_df=fx_rates_df,
	                                   dep_hist_df=deposits_df,
	                                   close_adj_df=close_adj_df)
	save_backtest_data(hist_portfolio_data=backtest_data)
	return backtest_data


def backtest_portfolio_benchmark(account: Accounts,
                                 index: pd.DatetimeIndex) -> PortfolioBacktestData:
	prices_adj_df = fetch_instr_adj_prices(account=account,
	                                       isin_lst=[v[2] for v in account.benchmark.values()],
	                                       tick_lst=list(account.benchmark.keys()))
	backtest_data_benchmark = backtest_portfolio(name=f'{account.name}_benchmark',
	                                             prices_df=prices_adj_df.reindex(index=index).ffill(),
	                                             target_exp=[v[0] for v in account.benchmark.values()],
	                                             curr_base=account.currency,
	                                             freq_rebalancing=[v[1] for v in account.benchmark.values()][0])
	save_backtest_data(hist_portfolio_data=backtest_data_benchmark)
	return backtest_data_benchmark


def backtest_portfolio_optimized(account: Accounts,
                                 index: pd.DatetimeIndex) -> PortfolioBacktestData:
	prices_adj_df = fetch_portfolio_instr_adj_prices(account=account)
	target_exp_df = compute_weights_optim_portfolio(allocation_method=AllocationStrats.MAX_SHARPE,
	                                                prices=prices_adj_df,
	                                                sampling_freq=DEFAULT_DATA_FREQ,
	                                                optimization_freq='M',
	                                                extra_args={'max_asset_exposure': 0.3,
	                                                            'max_vol': 0.15})
	portfolio_name = f'{account.name} Opt. (Tangency)'
	backtest_data_optimized = backtest_portfolio(name=portfolio_name,
	                                             prices_df=prices_adj_df.reindex(index=index).ffill(),
	                                             target_exp=target_exp_df,
	                                             curr_base=account.currency)
	results_dict = compute_results_from_navs(navs=backtest_data_optimized.nav,
	                                         file_name=portfolio_name)
	save_backtest_data(hist_portfolio_data=backtest_data_optimized)
	save_performance_data(results_dict=results_dict, file_name=portfolio_name)
	return backtest_data_optimized


class UnitTests(Enum):
	UPDATE_DATA = 1
	BACKTEST_PORTFOLIO_ACCOUNT = 2
	BACKTEST_PORTFOLIO_OPTIMIZED = 3


def run_unit_test(unit_test: UnitTests):
	if unit_test == UnitTests.UPDATE_DATA:
		for account in Accounts:
			update_data(account=account)
	elif unit_test == UnitTests.BACKTEST_PORTFOLIO_ACCOUNT:
		for account in Accounts:
			backtest_portfolio_account(account=account)
	elif unit_test == UnitTests.BACKTEST_PORTFOLIO_OPTIMIZED:
		for account in Accounts:
			index = load_backtest_data(account=account).nav_eff.index
			backtest_portfolio_optimized(account=account, index=index)
	else:
		raise NotImplementedError


if __name__ == '__main__':
	unit_test = UnitTests.BACKTEST_PORTFOLIO_OPTIMIZED
	run_unit_test(unit_test=unit_test)
