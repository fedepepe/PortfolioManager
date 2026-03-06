from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from degiro.transactions import TxHistFields
from portfolio.portfolio_generic import PortfolioGeneric
from utils.file_utils import PD_DATA_TYPES


class PortfolioDegiro(PortfolioGeneric):
	def rebalance(self,
	              tx_hist_df: Optional[PD_DATA_TYPES] = None):
		self.txn_value = np.zeros(len(self.tickers))
		self.txn_costs = np.zeros(len(self.tickers))
		self.current_units = self.previous_units.copy()
		for n in range(len(tx_hist_df)):
			idx = self.tickers.index(tx_hist_df.iloc[n, :][TxHistFields.product_id])
			# check that units reflect price directly
			quantity = tx_hist_df.iloc[n, :][TxHistFields.quantity]
			price = tx_hist_df.iloc[n, :][TxHistFields.price]
			total = tx_hist_df.iloc[n, :][TxHistFields.total]
			if quantity * price == - total:
				self.current_units[idx] += quantity
			else:
				self.current_units[idx] += - total / price
			self.txn_value[idx] += tx_hist_df.iloc[n, :][TxHistFields.total_in_base_currency]
			self.txn_costs[idx] += tx_hist_df.iloc[n, :][TxHistFields.total_fees_in_base_currency]
		self.add_cash(self.txn_value.sum() + self.txn_costs.sum())
		self.previous_units = self.current_units.copy()


class ProductTypes:
	STOCK = 'STOCK'
	ETF = 'ETF'
	BOND = 'BOND'
	WARRANT = 'WARRANT'
	INDEX = 'INDEX'
	CURRENCY = 'CURRENCY'
	FUTURE = 'FUTURE'
	OPTION = 'OPTION'
	FUND = 'FUND'
	LEVERAGED = 'LEVERAGED'
	CASH = 'CASH'


class Exchanges(Enum):
	XET = 194
	TDG = 196
	EAM = 200
	LSE = 570
	MIL = 608
	SWX = 947
