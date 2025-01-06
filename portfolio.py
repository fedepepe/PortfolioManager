import numpy as np
import pandas as pd

from definitions import BASE_CURRENCY, PORTFOLIO_NAME
from product_definitions import Currencies
from transactions import TxHistoryDataFields


class Portfolio:
    def __init__(self,
                 prices_df: pd.DataFrame,
                 name: str = PORTFOLIO_NAME,
                 base_currency: Currencies = BASE_CURRENCY,
                 initial_cash_balance: float = 1e6):
        self.name: str = name
        self.base_currency: Currencies = base_currency
        self.current_units: np.ndarray = np.zeros(prices_df.shape[1])
        self.current_cash_balance: float = initial_cash_balance
        self.previous_units: np.ndarray = np.zeros(prices_df.shape[1])
        self.transaction_value: float = 0.
        self.transaction_costs: float = 0.
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
        for n in range(len(tx_history_df)):
            if tx_history_df.iloc[n, :][TxHistoryDataFields.symbol] is np.nan:
                idx = self.prices.columns.to_list().index(tx_history_df.iloc[n, :][TxHistoryDataFields.product_id])
            else:
                idx = self.prices.columns.to_list().index(tx_history_df.iloc[n, :][TxHistoryDataFields.symbol])
            self.current_units = self.previous_units.copy()
            # check that units reflect price directly
            quantity = tx_history_df.iloc[n, :][TxHistoryDataFields.quantity]
            price = tx_history_df.iloc[n, :][TxHistoryDataFields.price]
            total = tx_history_df.iloc[n, :][TxHistoryDataFields.total]
            if quantity * price == - total:
                self.current_units[idx] += quantity
            else:
                self.current_units[idx] += - total / price
            self.transaction_value = tx_history_df.iloc[n, :][TxHistoryDataFields.total_in_base_currency]
            self.transaction_costs = tx_history_df.iloc[n, :][TxHistoryDataFields.total_fees_in_base_currency]
            self.current_cash_balance = self.current_cash_balance + self.transaction_value + self.transaction_costs
            self.previous_units = self.current_units.copy()
