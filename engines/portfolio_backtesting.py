import numpy as np
import pandas as pd

from portfolio.portfolio_generic import Portfolio


def compute_historical_portfolio(self):
	# restrict prices to chosen timeframe
	prices_df = self.StrategyInputData.prices.copy()
	if self.date_start is not None:
		prices_df = prices_df[prices_df.index >= self.date_start]
	if self.date_stop is not None:
		prices_df = prices_df[prices_df.index <= self.date_stop]
	assert prices_df.columns.to_list() == self.StrategyOutData.target_exposure.columns.to_list()
	rebalancing_dates = self.StrategyOutData.target_exposure.index
	assert all(rebalancing_dates == sorted(rebalancing_dates))

	# initialize
	units_assets = np.zeros_like(prices_df)
	effective_weights = np.zeros_like(prices_df)
	nav = np.zeros(len(prices_df))
	cash_balance = np.zeros(len(prices_df))
	transaction_amounts = []
	transaction_value = np.zeros(len(prices_df))
	transaction_costs = np.zeros(len(prices_df))

	# build initial portfolio
	portfolio = Portfolio(tickers=prices_df.columns.to_list(),
	                      initial_cash_balance=self.StrategyConf.initial_cash_pos)

	# loop over t
	for t in np.arange(0, len(prices_df)):
		current_prices = prices_df.iloc[t, :]

		# rebalance
		if prices_df.index[t] in rebalancing_dates:
			rebalancing_timestamp = prices_df.index[t]
			if prices_df.index[t] not in self.StrategyOutData.target_exposure.index:
				pass
			# print(f'No target exposure found for {rebalancing_timestamp}. Skipping...')
			else:
				if self.StrategyOutData.units is not None:
					units = self.StrategyOutData.target_exposure.loc[rebalancing_timestamp, :].copy()
					portfolio.rebalance(current_prices=current_prices, units=units)
				elif self.StrategyOutData.target_exposure is not None:
					target_weights = self.StrategyOutData.target_exposure.loc[rebalancing_timestamp, :].copy()
					portfolio.rebalance(current_prices=current_prices, target_exp=target_weights)
				transaction_amounts.append(portfolio.txn_value)
				transaction_value[t] = portfolio.transaction_value
				transaction_costs[t] = portfolio.txn_costs

		# store data
		units_assets[t, :] = portfolio.current_units
		cash_balance[t] = portfolio.get_current_cash_balance()
		effective_weights[t, :] = portfolio.get_effective_weights(current_prices=current_prices)
		nav[t] = portfolio.get_nav(current_prices=current_prices)

	# store data
	units_df = pd.DataFrame(units_assets, columns=prices_df.columns, index=prices_df.index)
	effective_weights_df = pd.DataFrame(effective_weights, columns=prices_df.columns, index=prices_df.index)
	units_df['Cash'] = cash_balance
	effective_weights_df['Cash'] = cash_balance / nav
	transaction_amounts_df = pd.DataFrame(transaction_amounts, columns=prices_df.columns, index=rebalancing_dates)
	self.StrategyOutData.nav = pd.Series(nav, name='NAV', index=prices_df.index)
	self.StrategyOutData.units = units_df
	self.StrategyOutData.effective_weights = effective_weights_df
	self.StrategyOutData.transaction_amounts = transaction_amounts_df
	self.StrategyOutData.transaction_value = pd.Series(transaction_value, name='Tx value', index=prices_df.index)
	self.StrategyOutData.transaction_costs = pd.Series(transaction_costs, name='Tx costs', index=prices_df.index)
	self.StrategyOutData.cum_pnl = (self.StrategyInputData.prices
	                                .diff()
	                                .mul(self.StrategyOutData.units.shift(1))
	                                .cumsum()
	                                .fillna(0)
	                                .reindex_like(self.StrategyOutData.units))
	# individual cumulative profit and loss per instrument
	self.StrategyOutData.cum_pnl['Tx costs'] = - (self.StrategyOutData.transaction_costs
	                                              .cumsum()
	                                              .reindex_like(self.StrategyOutData.units))
	# individual returns
	self.StrategyOutData.returns = (self.StrategyOutData.cum_pnl
	                                .resample(self.StrategyConf.rebalancing_freq).last()
	                                .diff()
	                                .div(self.StrategyOutData.nav.resample(self.StrategyConf.rebalancing_freq).last()
	                                     .shift(1),
	                                     axis=0))
