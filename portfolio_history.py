from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from charts import fetch_chart, save_charts, load_charts
from transactions import load_tx_history, fetch_tx_history, TxHistoryDataFields


class Portfolio:
	def __init__(self,
	             prices_df: pd.DataFrame,
	             initial_cash_balance=1e6):
		self.current_units = np.zeros(prices_df.shape[1])
		self.current_cash_balance = initial_cash_balance
		self.previous_units = np.zeros(prices_df.shape[1])
		self.transaction_value = 0.
		self.transaction_costs = 0.
		self.prices = prices_df

	def get_dollar_values(self, current_prices):
		return self.current_units * current_prices

	def get_nav(self, current_prices):
		return np.nansum(self.get_dollar_values(current_prices)) + self.current_cash_balance

	def get_effective_weights(self, current_prices):
		return self.get_dollar_values(current_prices) / self.get_nav(current_prices)

	def rebalance(self, tx_history_df: pd.Series):
		for n in range(len(tx_history_df)):
			idx = self.prices.columns.to_list().index(tx_history_df.iloc[n, :][TxHistoryDataFields.product_id])
			self.current_units = self.previous_units.copy()
			self.current_units[idx] += tx_history_df.iloc[n, :][TxHistoryDataFields.quantity]
			self.transaction_value = tx_history_df.iloc[n, :][TxHistoryDataFields.total_in_base_currency]
			self.transaction_costs = tx_history_df.iloc[n, :][TxHistoryDataFields.total_fees_in_base_currency]
			self.current_cash_balance = self.current_cash_balance + self.transaction_value + self.transaction_costs
			self.previous_units = self.current_units.copy()


def compute_hist_nav(prices_df: pd.DataFrame,
                     tx_history_df: pd.DataFrame,
                     fx_rates_df: Optional[pd.DataFrame] = None):
	product_ids = list(set(tx_history_df['product_id'].to_list()))

	# resample and restrict to a suitable timeframe
	tx_history_df['Date'] = [ts.replace(hour=0, minute=0, second=0, microsecond=0) for ts in tx_history_df.index]
	ts_start = tx_history_df['Date'][0]
	prices_df = prices_df.loc[prices_df.index >= ts_start, product_ids]

	# initialize
	units = np.zeros_like(prices_df)
	effective_weights = np.zeros_like(prices_df)
	nav = np.zeros(len(prices_df))
	cash_balance = np.zeros(len(prices_df))
	transaction_value = np.zeros(len(prices_df))
	transaction_costs = np.zeros(len(prices_df))

	# build initial portfolio
	portfolio = Portfolio(prices_df=prices_df, initial_cash_balance=4e4)

	# loop over t
	for t in np.arange(0, len(prices_df)):
		current_prices = prices_df.iloc[t, :]

		# rebalance
		if prices_df.index[t] in tx_history_df['Date'].to_list():
			portfolio.rebalance(tx_history_df=tx_history_df.loc[tx_history_df['Date'] == prices_df.index[t]])
			transaction_value[t] = portfolio.transaction_value
			transaction_costs[t] = portfolio.transaction_costs

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
	return nav, units_df, effective_weights_df, transaction_costs, transaction_value


def update_data():
	tx_history_df = fetch_tx_history()
	product_ids = list(set(tx_history_df['product_id'].to_list()))
	chart_df = fetch_chart(product_ids=product_ids)
	save_charts(chart_df)


def compute_portfolio_nav():
	tx_history_df = load_tx_history()
	chart_df = load_charts()
	compute_hist_nav(prices_df=chart_df, tx_history_df=tx_history_df)


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
