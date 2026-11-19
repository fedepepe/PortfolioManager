"""Monthly returns of the instruments of the Markets page (table market_instruments), from their stored Yahoo Finance
history.
"""

import logging
import time
from collections import Counter
from datetime import date

import pandas as pd

from portfolio_manager.market_data.history import (
    listing_currencies,
    load_adjusted_closes,
    refresh_history,
    refresh_info,
)
from portfolio_manager.storage.queries import query_market_instruments

logger = logging.getLogger(__name__)

MONTHS = 12  # months shown, the current one included


def market_tickers() -> list[str]:
    """Tickers of the instruments of the Markets page, in their order."""
    return [instrument.ticker for instrument in query_market_instruments()]


def refresh_market_data() -> Counter:
    """Bring the stored history of the instruments of the Markets page up to date (the whole history of a new one, with
    its information); returns the count of each result (new, updated, replaced, not found, failed).
    """
    tickers = market_tickers()
    known = listing_currencies(tickers)
    results = Counter()
    for ticker in tickers:
        try:
            result = refresh_history(ticker)
            if ticker not in known:
                refresh_info(ticker)
        except Exception as e:  # any download problem: the stored history is kept
            logger.warning('Yahoo Finance download failed for %s: %s', ticker, e)
            result = 'failed'
        results[result] += 1
        time.sleep(0.2)  # spread the requests to Yahoo Finance
    logger.info('Market data downloaded: %s', ', '.join(f'{k} {v}' for k, v in results.items()))
    return results


def monthly_returns(closes: pd.DataFrame, today: date, months: int = MONTHS) -> tuple[pd.DataFrame, pd.Series]:
    """Returns of each instrument (closes: date x ticker, adjusted for distributions) in each of the last months up to
    the month of today (the current month to date), and over all of them: ticker x month (periods), and the total by
    ticker. A month without a close of its own has no return; the total runs from the last close of the month before
    the first one to the last close.
    """
    periods = pd.period_range(end=pd.Period(today, freq='M'), periods=months, freq='M')
    returns, totals = {}, {}
    for ticker in closes.columns:
        series = closes[ticker].dropna()
        if series.empty:
            returns[ticker], totals[ticker] = pd.Series(index=periods, dtype=float), float('nan')
            continue
        month_last = series.groupby(pd.DatetimeIndex(series.index).to_period('M')).last()
        values = month_last.reindex(pd.PeriodIndex([periods[0] - 1, *periods]))
        returns[ticker] = (values / values.shift(1) - 1.0).iloc[1:]
        known = values.iloc[1:].dropna()
        totals[ticker] = known.iloc[-1] / values.iloc[0] - 1.0 if not known.empty else float('nan')
    return pd.DataFrame(returns, index=periods).T, pd.Series(totals, dtype=float)


def market_returns(today: date | None = None) -> tuple[pd.DataFrame, pd.Series, pd.Timestamp | None]:
    """Monthly returns of the instruments of the Markets page (see monthly_returns) from their stored history, in their
    order (an instrument without history has no returns), and the day of the last stored close.
    """
    tickers = market_tickers()
    closes = load_adjusted_closes(tickers).reindex(columns=tickers)
    returns, totals = monthly_returns(closes, today or date.today())
    last_day = closes.dropna(how='all').index.max() if not closes.dropna(how='all').empty else None
    return returns.reindex(tickers), totals.reindex(tickers), last_day
