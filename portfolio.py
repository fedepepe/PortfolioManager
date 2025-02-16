from typing import NamedTuple, Optional

import numpy as np
import pandas as pd

from definitions import DEFAULT_PORTFOLIO_NAME
from product_definitions import Currencies
from transactions import TxHistFields


class Portfolio:
    def __init__(self,
                 prices_df: pd.DataFrame,
                 base_currency: Currencies,
                 name: str = DEFAULT_PORTFOLIO_NAME,
                 initial_cash_balance: float = 1e6):
        self.name: str = name
        self.base_currency: Currencies = base_currency
        self.current_units: np.ndarray = np.zeros(prices_df.shape[1])
        self.current_cash_balance: float = initial_cash_balance
        self.previous_units: np.ndarray = np.zeros(prices_df.shape[1])
        self.txn_value: np.ndarray = np.zeros(prices_df.shape[1])
        self.txn_costs: np.ndarray = np.zeros(prices_df.shape[1])
        self.prices: pd.DataFrame = prices_df

    def get_dollar_values(self, current_prices) -> np.ndarray:
        return self.current_units * current_prices

    def get_nav(self, current_prices) -> float:
        return np.nansum(self.get_dollar_values(current_prices)) + self.current_cash_balance

    def get_effective_weights(self, current_prices) -> np.ndarray:
        return self.get_dollar_values(current_prices) / self.get_nav(current_prices)

    def add_cash(self, value: float):
        self.current_cash_balance += value

    def rebalance(self, tx_history_df: pd.Series):
        self.txn_value = np.zeros(self.prices.shape[1])
        self.txn_costs = np.zeros(self.prices.shape[1])
        self.current_units = self.previous_units.copy()
        for n in range(len(tx_history_df)):
            if tx_history_df.iloc[n, :][TxHistFields.symbol] is np.nan:
                idx = self.prices.columns.to_list().index(tx_history_df.iloc[n, :][TxHistFields.product_id])
            else:
                idx = self.prices.columns.to_list().index(tx_history_df.iloc[n, :][TxHistFields.symbol])
            # check that units reflect price directly
            quantity = tx_history_df.iloc[n, :][TxHistFields.quantity]
            price = tx_history_df.iloc[n, :][TxHistFields.price]
            total = tx_history_df.iloc[n, :][TxHistFields.total]
            if quantity * price == - total:
                self.current_units[idx] += quantity
            else:
                self.current_units[idx] += - total / price
            self.txn_value[idx] += tx_history_df.iloc[n, :][TxHistFields.total_in_base_currency]
            self.txn_costs[idx] += tx_history_df.iloc[n, :][TxHistFields.total_fees_in_base_currency]
        self.current_cash_balance = self.current_cash_balance + self.txn_value.sum() + self.txn_costs.sum()
        self.previous_units = self.current_units.copy()


class HistPortfolioData(NamedTuple):
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
    close_adj: Optional[pd.Series] = None
    freq: Optional[str] = 'B'
