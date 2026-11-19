"""Signals of the research, computed every business day from the excess return index of each instrument (date x ticker)
with the data up to that day only: a higher score means a better expected return.
"""

import numpy as np
import pandas as pd

DAYS_PER_MONTH = 21  # business days


def momentum(index: pd.DataFrame, months: int, skip_months: int = 0) -> pd.DataFrame:
    """Return over the last months, leaving out the most recent skip_months (e.g. 12-1: a year without its last
    month, which tends to reverse).
    """
    return index.shift(skip_months * DAYS_PER_MONTH) / index.shift(months * DAYS_PER_MONTH) - 1.0


def ma_crossover(index: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Gap between a fast and a slow moving average (in business days; fast 1: the price itself), positive in an
    uptrend.
    """
    fast_average = index if fast == 1 else index.rolling(fast).mean()
    return fast_average / index.rolling(slow).mean() - 1.0


def reversal(index: pd.DataFrame, days: int) -> pd.DataFrame:
    """Minus the return over the last days: the instruments that fell the most score the highest."""
    return 1.0 - index / index.shift(days)


def zscore_reversal(index: pd.DataFrame, window: int) -> pd.DataFrame:
    """Minus the distance from the moving average, in standard deviations over the same window: positive below the
    average.
    """
    average, deviation = index.rolling(window).mean(), index.rolling(window).std()
    return (average - index) / deviation.replace(0.0, np.nan)


def low_volatility(excess_returns: pd.DataFrame, months: int = 12) -> pd.DataFrame:
    """Minus the volatility of the daily excess returns over the last months: the calmest instruments score the
    highest.
    """
    days = months * DAYS_PER_MONTH
    return -excess_returns.rolling(days, min_periods=days * 3 // 4).std()


def high_proximity(index: pd.DataFrame, days: int = 252) -> pd.DataFrame:
    """Distance from the highest price of the last days (52 weeks by default), 0 at a new high."""
    return index / index.rolling(days).max() - 1.0


def volatility(excess_returns: pd.DataFrame, days: int = 3 * DAYS_PER_MONTH) -> pd.DataFrame:
    """Annualized volatility of the daily excess returns over the last days."""
    return excess_returns.rolling(days, min_periods=days // 2).std() * np.sqrt(261)
