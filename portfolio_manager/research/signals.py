"""Signals of the research, computed every business day from the excess return index of each instrument (date x ticker)
with the data up to that day only: a higher score means a better expected return.

References of the single-instrument (time-series) signals:
- Moskowitz, Ooi and Pedersen (2012), "Time series momentum", Journal of Financial Economics: each instrument held long
  or short by the sign of its own past return, positions scaled by its volatility.
- Baltas and Kosowski (2013), "Momentum strategies in futures markets and trend-following funds": continuous trend
  measures, which size positions by the strength of the trend instead of its sign.
- Brock, Lakonishok and LeBaron (1992), "Simple technical trading rules and the stochastic properties of stock
  returns", Journal of Finance: moving-average crossover rules (e.g. 50/200 days, the "golden cross").
- Carver (2015), "Systematic Trading": the exponentially weighted moving-average crossover (EWMAC), the gap between a
  fast and a slow average divided by the price volatility; forecasts scaled to a fixed average size and capped.
- Bollinger (2001), "Bollinger on Bollinger Bands": the distance of the price from its moving average in standard
  deviations.
- Zakamulin (2017), "Market Timing with Moving Averages": every moving-average rule is a weighted average of past
  returns, a form of momentum.
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


VOL_DAYS = 3 * DAYS_PER_MONTH  # days of the volatility that scales the risk-adjusted signals (at least)
PRICE_VOL_SPAN = 36  # span (days) of the exponentially weighted price volatility of the crossover signals
FORECAST_CAP = 2.0  # cap of the scaled forecasts (Carver: 20 on a scale averaging 10)


def risk_adjusted_return(index: pd.DataFrame, excess_returns: pd.DataFrame, days: int) -> pd.DataFrame:
    """Return over the last days divided by the volatility expected over as many days (daily volatility times
    sqrt(days)): a t-statistic-like trend measure that makes calm and volatile instruments comparable (Moskowitz, Ooi
    and Pedersen 2012 scale by volatility; Baltas and Kosowski 2013 size by the trend's strength).
    """
    window = max(days, VOL_DAYS)
    daily_vol = excess_returns.rolling(window, min_periods=window * 3 // 4).std()
    return (index / index.shift(days) - 1.0) / (daily_vol * np.sqrt(days))


def price_volatility(index: pd.DataFrame, excess_returns: pd.DataFrame, span: int = PRICE_VOL_SPAN) -> pd.DataFrame:
    """Volatility of the daily price changes in price units: the price times the exponentially weighted volatility of
    the daily returns (the denominator of Carver's EWMAC).
    """
    return index * excess_returns.ewm(span=span, min_periods=span).std()


def ewmac(index: pd.DataFrame, excess_returns: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Exponentially weighted moving-average crossover (Carver 2015): the fast minus the slow exponential average of
    the price (spans in days, e.g. 16/64), divided by the price volatility.
    """
    gap = index.ewm(span=fast, min_periods=slow).mean() - index.ewm(span=slow, min_periods=slow).mean()
    return gap / price_volatility(index, excess_returns)


def sma_crossover_gap(index: pd.DataFrame, excess_returns: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Gap between a fast and a slow simple moving average (Brock, Lakonishok and LeBaron 1992, e.g. 50/200 days),
    divided by the price volatility as in Carver's EWMAC, to size the position by the strength of the trend.
    """
    gap = index.rolling(fast).mean() - index.rolling(slow).mean()
    return gap / price_volatility(index, excess_returns)


def bollinger_reversal(index: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Minus the distance of the price from its moving average in standard deviations of the price over the same
    window (Bollinger 2001: the bands lie 2 deviations from a 20-day average): positive below the average.
    """
    return zscore_reversal(index, window)


def scale_forecast(score: pd.DataFrame, cap: float = FORECAST_CAP) -> pd.DataFrame:
    """A signal rescaled to an average absolute value of 1 and capped at +/- cap (Carver 2015: forecasts averaging 10,
    capped at 20). The average is that of all the instruments up to each day: no future data is used.
    """
    daily = score.abs().mean(axis=1)
    scalar = daily.expanding(min_periods=DAYS_PER_MONTH).mean()
    return score.div(scalar.where(scalar > 0), axis=0).clip(-cap, cap)


def volatility(excess_returns: pd.DataFrame, days: int = 3 * DAYS_PER_MONTH) -> pd.DataFrame:
    """Annualized volatility of the daily excess returns over the last days."""
    return excess_returns.rolling(days, min_periods=days // 2).std() * np.sqrt(261)
