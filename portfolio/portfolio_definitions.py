from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

DEFAULT_FREQ_HIST_DATA = 'B'


class Currencies:
    EUR = 'EUR'
    USD = 'USD'
    CHF = 'CHF'


class PortfolioGeneric:
    def __init__(
        self,
        tickers: list[str],
        base_currency: Currencies = Currencies.USD,
        name: str = 'Portfolio',
        initial_cash_balance: float = 1e6,
        max_target_dev: float = 0.0,
        txn_costs_prop_bp: int = 0,  # proportional transaction costs in basis points
        txn_costs_fixed: float = 0.0,  # fixed transaction costs per asset traded
        txn_costs_max: float | None = None,  # max transaction costs per asset traded (None: no cap)
        min_cash_amount: float = 0.0,
        min_cash_ratio: float = 0.0,
    ):  # cash kept at rebalancing, as a fraction of the NAV
        self.tickers = tickers
        self.name: str = name
        self.currency_base: Currencies = base_currency
        self.current_units: np.ndarray = np.zeros(len(tickers))
        self.__current_cash_balance: float = initial_cash_balance
        self.previous_units: np.ndarray = np.zeros(len(tickers))
        self.txn_values: np.ndarray = np.zeros(len(tickers))
        self.txn_costs: np.ndarray = np.zeros(len(tickers))
        self.max_target_dev = max_target_dev
        self.txn_costs_prop = txn_costs_prop_bp / 1e4
        self.txn_costs_fixed = txn_costs_fixed
        self.txn_costs_max = txn_costs_max
        self.min_cash_amount = min_cash_amount
        self.min_cash_ratio = min_cash_ratio

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
    def rebalance(
        self,
        current_prices: np.ndarray | pd.Series | None = None,
        target_exp: np.ndarray | pd.Series | None = None,
        units: np.ndarray | pd.Series | None = None,
        rebalance_type: PortfolioRebalanceType = PortfolioRebalanceType.MINIMAL,
    ):
        if target_exp is None and units is None:
            raise AttributeError('At least one between target_exp and units must be provided.')
        # estimate transaction costs
        self.txn_costs = 0 * current_prices
        current_units = self.compute_current_units(current_prices, target_exp, units, rebalance_type=rebalance_type)
        txn_values = self.compute_txn_values(current_prices, current_units)
        self.txn_costs = self.compute_txn_costs(txn_values)
        # compute effective trades
        current_units = self.compute_current_units(current_prices, target_exp, units, rebalance_type=rebalance_type)
        txn_values = self.compute_txn_values(current_prices, current_units)
        if self.get_current_cash_balance() + np.nansum(txn_values - self.compute_txn_costs(txn_values)) < 0:
            current_units = self.compute_current_units(
                current_prices, target_exp, units, rebalance_type=PortfolioRebalanceType.FULL
            )
            txn_values = self.compute_txn_values(current_prices, current_units)
        self.current_units = current_units
        self.txn_values = txn_values
        self.txn_costs = self.compute_txn_costs(txn_values)
        self.add_cash(self.txn_values.sum() - self.txn_costs.sum())
        self.previous_units = self.current_units.copy()

    def compute_txn_costs(self, txn_values: np.ndarray | pd.Series) -> np.ndarray | pd.Series:
        # proportional costs, plus the fixed cost of each instrument actually traded, capped per instrument
        txn_costs = self.txn_costs_prop * np.abs(txn_values) + self.txn_costs_fixed * (txn_values != 0)
        if self.txn_costs_max is not None:
            txn_costs = np.minimum(txn_costs, self.txn_costs_max)
        return txn_costs

    def compute_txn_values(
        self, current_prices: np.ndarray | pd.Series, current_units: np.ndarray | pd.Series
    ) -> np.ndarray | pd.Series:
        change_units = current_units - self.previous_units
        txn_values = -change_units * current_prices
        return txn_values

    def compute_current_units(
        self,
        current_prices: np.ndarray | pd.Series,
        target_exp: np.ndarray | pd.Series | None = None,
        units: np.ndarray | pd.Series | None = None,
        rebalance_type: PortfolioRebalanceType = PortfolioRebalanceType.MINIMAL,
    ) -> np.ndarray | pd.Series:
        if target_exp is not None:
            if rebalance_type == PortfolioRebalanceType.MINIMAL:
                bool_trade = np.abs(self.get_effective_weights(current_prices) - target_exp) > self.max_target_dev
            elif rebalance_type == PortfolioRebalanceType.FULL:
                bool_trade = pd.Series(True, index=current_prices.index)
            else:
                raise AttributeError
            nav_available = (
                self.get_nav(current_prices) * (1.0 - self.min_cash_ratio) - self.min_cash_amount - self.txn_costs.sum()
            )
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
    units: pd.DataFrame
    cum_pnl: pd.DataFrame = None
    yield_dividends: pd.DataFrame = None
    yield_total: pd.DataFrame = None
    target_weights: pd.DataFrame | None = None
    effective_weights: pd.DataFrame | None = None
    transaction_value: pd.DataFrame | None = None
    transaction_costs: pd.DataFrame | None = None
    prices: pd.DataFrame | None = None
    dividends: pd.DataFrame | None = None
    fx_rates: pd.DataFrame | None = None
    deposits: pd.Series | None = None
    nav_eff: pd.Series | None = None
    close_adj: pd.DataFrame | None = None
    freq: str | None = DEFAULT_FREQ_HIST_DATA
    id_symbol_map: dict | None = None
