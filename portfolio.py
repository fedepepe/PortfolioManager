from typing import NamedTuple, Optional, List

import numpy as np
import pandas as pd

from file_utils import PD_DATA_TYPES
from product_definitions import Currencies
from transactions import TxHistFields


class Portfolio:
    def __init__(self,
                 tickers: List[str],
                 base_currency: Currencies = Currencies.USD,
                 name: str = 'Portfolio',
                 initial_cash_balance: float = 1e6):
        self.tickers = tickers
        self.name: str = name
        self.currency_base: Currencies = base_currency
        self.current_units: np.ndarray = np.zeros(len(tickers))
        self.__current_cash_balance: float = initial_cash_balance
        self.previous_units: np.ndarray = np.zeros(len(tickers))
        self.txn_value: np.ndarray = np.zeros(len(tickers))
        self.txn_costs: np.ndarray = np.zeros(len(tickers))

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

    def rebalance(self,
                  target_exp: Optional[np.ndarray | pd.Series] = None,
                  units: Optional[np.ndarray | pd.Series] = None,
                  tx_hist_df: Optional[PD_DATA_TYPES] = None,
                  current_prices: Optional[np.ndarray | pd.Series] = None):
        self.txn_value = np.zeros(len(self.tickers))
        self.txn_costs = np.zeros(len(self.tickers))
        self.current_units = self.previous_units.copy()
        if target_exp is not None:
            self.current_units = (self.get_nav(current_prices) * target_exp) / current_prices
            self.current_units[np.isnan(self.current_units)] = 0
            change_units = self.current_units - self.previous_units
            self.txn_value = - np.nansum(change_units * current_prices)
        elif units is not None:
            self.current_units = units.values
            change_units = self.current_units - self.previous_units
            self.txn_value = - np.nansum(change_units * current_prices)
        elif tx_hist_df is not None:
            for n in range(len(tx_hist_df)):
                if tx_hist_df.iloc[n, :][TxHistFields.symbol] is np.nan:
                    idx = self.tickers.index(tx_hist_df.iloc[n, :][TxHistFields.product_id])
                else:
                    idx = self.tickers.index(tx_hist_df.iloc[n, :][TxHistFields.symbol])
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
