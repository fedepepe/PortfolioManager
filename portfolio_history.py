from enum import Enum
from typing import Optional, NamedTuple

import numpy as np
import pandas as pd

import file_utils as fu
from date_utils import reset_time
from charts import fetch_portfolio_charts, load_portfolio_charts, fetch_fx_charts
from definitions import DATA_DIR, PORTFOLIO_NAME, BASE_CURRENCY, FX_RATES_CHART_FILE_NAME
from portfolio import Portfolio
from products import fetch_portfolio_products, load_portfolio_products
from transactions import fetch_account_movements, load_account_movements
from transactions import fetch_tx_history, load_tx_history


class HistPortfolioData(NamedTuple):
	nav: pd.Series
	units: pd.DataFrame
	target_weights: Optional[pd.DataFrame]
	effective_weights: pd.DataFrame
	transaction_costs: pd.Series
	transaction_value: pd.Series
	prices: Optional[pd.DataFrame] = None
	dividends: Optional[pd.Series] = None
	fx_rates: Optional[pd.DataFrame] = None
	deposits: Optional[pd.Series] = None
	nav_eff: Optional[pd.Series] = None
	freq: Optional[str] = 'B'


def compute_hist_nav(prices_df: pd.DataFrame,
                     tx_history_df: pd.DataFrame,
                     initial_cash_balance: float = 1e4,
                     dividends_df: Optional[pd.DataFrame] = None,
                     fx_rates_df: Optional[pd.DataFrame] = None,
                     deposits_df: Optional[pd.DataFrame] = None,
                     ) -> HistPortfolioData:
	# initialize
	units = np.zeros_like(prices_df)
	effective_weights = np.zeros_like(prices_df)
	nav = np.zeros(len(prices_df))
	cash_balance = np.zeros(len(prices_df))
	transaction_value = np.zeros(len(prices_df))
	transaction_costs = np.zeros(len(prices_df))
	dividends = np.zeros(len(prices_df))
	deposits = np.zeros(len(prices_df))

	# build initial portfolio
	portfolio = Portfolio(prices_df=prices_df, initial_cash_balance=initial_cash_balance)

	# loop over t
	for t in np.arange(0, len(prices_df)):
		current_prices = prices_df.iloc[t, :]

		# rebalance
		if prices_df.index[t] in tx_history_df['Date'].to_list():
			portfolio.rebalance(tx_history_df=tx_history_df.loc[tx_history_df['Date'] == prices_df.index[t]])
			transaction_value[t] = portfolio.transaction_value
			transaction_costs[t] = portfolio.transaction_costs

		# add dividends
		if prices_df.index[t] in dividends_df['Date'].to_list():
			dividend = dividends_df.loc[dividends_df['Date'] == prices_df.index[t], 'amount_base_currency'].sum()
			portfolio.add_cash(dividend)
			dividends[t] = dividend

		# add deposits and subtract withdrawals
		if prices_df.index[t] in deposits_df['Date'].to_list():
			deposit = deposits_df.loc[deposits_df['Date'] == prices_df.index[t], 'change'].sum()
			portfolio.add_cash(deposit)
			deposits[t] = deposit

		# store
		units[t, :] = portfolio.current_units
		cash_balance[t] = portfolio.current_cash_balance
		effective_weights[t, :] = portfolio.get_effective_weights(current_prices=current_prices)
		nav[t] = portfolio.get_nav(current_prices=current_prices)

	units_df = pd.DataFrame(units, columns=prices_df.columns, index=prices_df.index)
	effective_weights_df = pd.DataFrame(effective_weights, columns=prices_df.columns, index=prices_df.index)
	units_df['Cash'] = cash_balance
	effective_weights_df['Cash'] = cash_balance / nav
	nav = pd.Series(nav, name='NAV', index=prices_df.index)
	transaction_value = pd.Series(transaction_value, name='Tx value', index=prices_df.index)
	transaction_costs = pd.Series(transaction_costs, name='Tx costs', index=prices_df.index)
	dividends = pd.Series(dividends, name='Dividends', index=prices_df.index)
	deposits = pd.Series(deposits, name='Deposits', index=prices_df.index)
	returns = (nav - deposits).div(nav.shift(1)).sub(1.).fillna(0.)
	nav_eff = 100. * returns.add(1.).cumprod().rename('NAV Effective')

	hist_portfolio_data = HistPortfolioData(nav=nav,
	                                        units=units_df,
	                                        target_weights=None,
	                                        effective_weights=effective_weights_df,
	                                        transaction_costs=transaction_costs,
	                                        transaction_value=transaction_value,
	                                        prices=prices_df,
	                                        dividends=dividends,
	                                        fx_rates=fx_rates_df,
	                                        deposits=deposits,
	                                        nav_eff=nav_eff)
	return hist_portfolio_data


def save_hist_portfolio_data(hist_portfolio_data: HistPortfolioData):
	fu.save_df_dict_to_excel(df_dict=hist_portfolio_data._asdict(),
	                         file_name=PORTFOLIO_NAME,
	                         folder=DATA_DIR)


def load_hist_portfolio_data() -> HistPortfolioData:
	data_dict = fu.load_df_dict_from_excel(file_name=PORTFOLIO_NAME, folder=DATA_DIR)
	return HistPortfolioData(**data_dict)


def update_data():
	fetch_tx_history()
	fetch_portfolio_products()
	fetch_account_movements()
	fetch_portfolio_charts()
	fetch_fx_charts()


def compute_portfolio_nav() -> HistPortfolioData:
	# prices
	prices_df = load_portfolio_charts()
	# transaction history
	tx_history_df = load_tx_history()
	# dividends
	account_mvmts_df = load_account_movements()
	dividends_df = account_mvmts_df.loc[account_mvmts_df['description'].isin(['Dividendo', 'Cedola']), :]
	# deposits/withdrawals
	deposits_df = account_mvmts_df.loc[((account_mvmts_df['description'].str.startswith('Deposito', na=False)) |
	                                    (account_mvmts_df['description'].str.startswith('Prelievo', na=False))), :]
	# forex rates
	products_df = load_portfolio_products()
	curr_foreign = list(set(products_df['currency'].to_list() + dividends_df['currency'].to_list()))
	curr_foreign = [c for c in curr_foreign if c != BASE_CURRENCY]
	fx_rates_df_tmp = load_portfolio_charts(chart_name=FX_RATES_CHART_FILE_NAME)
	fx_rates_df = pd.DataFrame()
	for cur in curr_foreign:
		if f'{cur}/{BASE_CURRENCY}' in fx_rates_df_tmp:
			fx_rates_df = pd.concat([fx_rates_df, fx_rates_df_tmp[f'{cur}/{BASE_CURRENCY}']], axis=1)
		elif f'{BASE_CURRENCY}/{cur}' in fx_rates_df_tmp:
			ser = (1. / fx_rates_df_tmp[f'{BASE_CURRENCY}/{cur}']).rename(f'{cur}/{BASE_CURRENCY}')
			fx_rates_df = pd.concat([fx_rates_df, ser], axis=1)
		else:
			raise Exception(f'Missing forex data for {cur}/{BASE_CURRENCY}!')
	if fx_rates_df.empty:
		fx_rates_df = fx_rates_df.reindex(index=prices_df.index)
	fx_rates_df[f'{BASE_CURRENCY}/{BASE_CURRENCY}'] = 1.0

	product_ids = list(set(tx_history_df['product_id'].to_list()))
	product_symbols = products_df.loc[product_ids, 'symbol'].to_list()
	product_curr = products_df.loc[product_ids, 'currency'].to_list()

	# compute initial cash balance
	initial_cash_balance = deposits_df.loc[deposits_df.index <= tx_history_df.index[0], 'change'].sum()

	# reset datetime and restrict to a suitable timeframe
	tx_history_df['symbol'] = products_df.loc[tx_history_df['product_id'], 'symbol'].to_list()
	tx_history_df.loc[:, 'Date'] = [reset_time(ts) for ts in tx_history_df.index]
	dividends_df.loc[:, 'Date'] = [reset_time(ts) for ts in dividends_df['value_date']]
	deposits_df.loc[:, 'Date'] = [reset_time(ts) for ts in deposits_df['value_date']]
	ts_start = tx_history_df['Date'].iloc[0]
	prices_df = prices_df.loc[prices_df.index >= ts_start, product_symbols].ffill()
	fx_rates_df = fx_rates_df.loc[fx_rates_df.index >= ts_start, :].reindex(prices_df.index).ffill()
	deposits_df = deposits_df.loc[deposits_df.index >= ts_start, :]

	# check all dividend dates are in the price datetime index
	assert all([d in prices_df.index for d in dividends_df['Date']])
	assert all([d in prices_df.index for d in deposits_df['Date']])

	# currency conversion
	for symbol, cur in zip(product_symbols, product_curr):
		prices_df.loc[:, symbol] = prices_df[symbol].mul(fx_rates_df.loc[prices_df.index, f'{cur}/{BASE_CURRENCY}'])
	fx_rates = [fx_rates_df.loc[dividends_df.iloc[n]['Date'], f'{cur}/{BASE_CURRENCY}']
	            for n, cur in enumerate(dividends_df['currency'])]
	dividends_df.loc[:, 'fx_rate'] = fx_rates
	dividends_df.loc[:, 'amount_base_currency'] = dividends_df['change'].mul(dividends_df['fx_rate'])

	# compute historical portfolio data
	hist_portfolio_data = compute_hist_nav(prices_df=prices_df,
	                                       tx_history_df=tx_history_df,
	                                       initial_cash_balance=initial_cash_balance,
	                                       dividends_df=dividends_df,
	                                       fx_rates_df=fx_rates_df,
	                                       deposits_df=deposits_df)
	save_hist_portfolio_data(hist_portfolio_data)
	return hist_portfolio_data


class UnitTests(Enum):
	UPDATE_DATA = 1
	COMPUTE_NAV = 2


def run_unit_test(unit_test: UnitTests):
	if unit_test == UnitTests.UPDATE_DATA:
		update_data()
	elif unit_test == UnitTests.COMPUTE_NAV:
		compute_portfolio_nav()


if __name__ == '__main__':
	unit_test = UnitTests.COMPUTE_NAV
	run_unit_test(unit_test=unit_test)
