from typing import Optional

import numpy as np
import pandas as pd

from degiro.transactions import TxHistFields
from portfolio.portfolio_definitions import PortfolioGeneric


class PortfolioDegiro(PortfolioGeneric):
	def rebalance(self,
	              tx_hist_df: Optional[pd.DataFrame] = None):
		self.txn_values = np.zeros(len(self.tickers))
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
			self.txn_values[idx] += tx_hist_df.iloc[n, :][TxHistFields.total_in_base_currency]
			self.txn_costs[idx] += tx_hist_df.iloc[n, :][TxHistFields.total_fees_in_base_currency]
		self.add_cash(self.txn_values.sum() + self.txn_costs.sum())
		self.previous_units = self.current_units.copy()
