from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict
from enum import Enum

import numpy as np
import pandas as pd


class Currencies:
	EUR = 'EUR'
	USD = 'USD'
	CHF = 'CHF'


class PortfolioGeneric:
	def __init__(self,
	             tickers: List[str],
	             base_currency: Currencies = Currencies.USD,
	             name: str = 'Portfolio',
	             initial_cash_balance: float = 1e6,
	             max_target_dev: float = 0.,
	             txn_costs_prop_bp: int = 0,  # proportional transaction costs in basis points
	             txn_costs_fixed: float = 0.,
	             min_cash_amount: float = 100.):
		self.tickers = tickers
		self.name: str = name
		self.currency_base: Currencies = base_currency
		self.current_units: np.ndarray = np.zeros(len(tickers))
		self.__current_cash_balance: float = initial_cash_balance
		self.previous_units: np.ndarray = np.zeros(len(tickers))
		self.txn_values: np.ndarray = np.zeros(len(tickers))
		self.txn_costs: np.ndarray = np.zeros(len(tickers))
		self.max_target_dev = max_target_dev
		self.txn_costs_prop = txn_costs_prop_bp / 10e4
		self.txn_costs_fixed = txn_costs_fixed
		self.min_cash_amount = min_cash_amount

	def get_dollar_values(self, current_prices) -> np.ndarray:
		return self.current_units * current_prices

	def get_nav(self, current_prices) -> float:
		return np.nansum(self.get_dollar_values(current_prices)) + self.__current_cash_balance

	def get_effective_weights(self, current_prices) -> np.ndarray:
		return self.get_dollar_values(current_prices) / self.get_nav(current_prices)

	def add_cash(self, value: float):
		self.__current_cash_balance += value

	def get_current_cash_balance(self):
		return self.__current_cash_balance

	@abstractmethod
	def rebalance(self):
		pass


class PortfolioRebalanceType(Enum):
	FULL = 'full'
	MINIMAL = 'minimal'


class Portfolio(PortfolioGeneric):
	def rebalance(self,
	              current_prices: Optional[np.ndarray | pd.Series] = None,
	              target_exp: Optional[np.ndarray | pd.Series] = None,
	              units: Optional[np.ndarray | pd.Series] = None,
	              rebalance_type: PortfolioRebalanceType = PortfolioRebalanceType.MINIMAL):
		if target_exp is None and units is None:
			raise AttributeError('At least one between target_exp and units must be provided.')
		# estimate transaction costs
		self.txn_costs = 0 * current_prices
		current_units = self.compute_current_units(current_prices, target_exp, units, rebalance_type=rebalance_type)
		txn_values = self.compute_txn_values(current_prices, current_units)
		self.txn_costs = self.txn_costs_prop * np.abs(txn_values) + self.txn_costs_fixed
		# compute effective trades
		current_units = self.compute_current_units(current_prices, target_exp, units, rebalance_type=rebalance_type)
		txn_values = self.compute_txn_values(current_prices, current_units)
		if self.get_current_cash_balance() + np.nansum(txn_values) < 0:
			current_units = self.compute_current_units(current_prices, target_exp, units,
			                                           rebalance_type=PortfolioRebalanceType.FULL)
			txn_values = self.compute_txn_values(current_prices, current_units)
		self.current_units = current_units
		self.txn_values = txn_values
		self.txn_costs = self.txn_costs_prop * np.abs(txn_values) + self.txn_costs_fixed * (txn_values != 0)
		self.add_cash(self.txn_values.sum() - self.txn_costs.sum())
		self.previous_units = self.current_units.copy()

	def compute_txn_values(self, current_prices: np.ndarray | pd.Series, current_units: np.ndarray | pd.Series
	                       ) -> np.ndarray | pd.Series:
		change_units = current_units - self.previous_units
		txn_values = - change_units * current_prices
		return txn_values

	def compute_current_units(self,
	                          current_prices: np.ndarray | pd.Series,
	                          target_exp: Optional[np.ndarray | pd.Series] = None,
	                          units: Optional[np.ndarray | pd.Series] = None,
	                          rebalance_type: PortfolioRebalanceType = PortfolioRebalanceType.MINIMAL
	                          ) -> np.ndarray | pd.Series:
		if target_exp is not None:
			if rebalance_type == PortfolioRebalanceType.MINIMAL:
				bool_trade = np.abs(self.get_effective_weights(current_prices) - target_exp) > self.max_target_dev
			elif rebalance_type == PortfolioRebalanceType.FULL:
				bool_trade = pd.Series(True, index=current_prices.index)
			else:
				raise AttributeError
			nav_available = self.get_nav(current_prices) - self.min_cash_amount - self.txn_costs.sum()
			current_units = (nav_available * target_exp) / current_prices
			current_units[~bool_trade] = self.previous_units[~bool_trade]
			current_units[np.isnan(current_units)] = 0
		elif units is not None:
			current_units = units.values
		else:
			raise AttributeError
		return current_units


@dataclass
class PortfolioBacktestData:
	name: str
	nav: pd.Series
	cum_pnl: pd.DataFrame
	div_yield: pd.DataFrame
	units: pd.DataFrame
	target_weights: Optional[pd.DataFrame] = None
	effective_weights: Optional[pd.DataFrame] = None
	transaction_costs: Optional[pd.DataFrame] = None
	transaction_value: Optional[pd.DataFrame] = None
	prices: Optional[pd.DataFrame] = None
	dividends: Optional[pd.DataFrame] = None
	fx_rates: Optional[pd.DataFrame] = None
	deposits: Optional[pd.Series] = None
	nav_eff: Optional[pd.Series] = None
	close_adj: Optional[pd.DataFrame] = None
	freq: Optional[str] = 'B'
	id_symbol_map: Optional[Dict] = None
