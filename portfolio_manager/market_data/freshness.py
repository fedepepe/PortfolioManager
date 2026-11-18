"""When the prices of a trading day are final, and which listings miss them.

The prices of a trading day count as final from FINAL_HOUR (local time) of the next day, on every exchange: by then the
exchanges have closed and Yahoo Finance has published their daily bars (sometimes late in the evening for European
listings). Holidays are not known: on a holiday a listing looks due, its download finds nothing new, and it is tried
again later.
"""

import logging
from collections import Counter
from datetime import date, datetime, time, timedelta

from portfolio_manager.market_data.history import listing_currencies, refresh_history, refresh_info
from portfolio_manager.storage.queries import query_last_close_dates
from portfolio_manager.utils.locks import key_lock

logger = logging.getLogger(__name__)

FINAL_HOUR = 8  # the prices of a trading day are final from this hour of the next day
# a listing without a price for longer is no longer updated (the candidates of the allocations leave it out too, see
# analytics.instruments.MAX_DAYS_WITHOUT_PRICE)
STALE_AFTER_DAYS = 30


def latest_final_day(now: datetime | None = None) -> date:
    """The latest trading day (Monday to Friday) whose prices are final at a moment (now if None)."""
    now = now or datetime.now()
    day = now.date() - timedelta(days=1 if now.hour >= FINAL_HOUR else 2)
    while day.weekday() >= 5:  # Saturday, Sunday
        day -= timedelta(days=1)
    return day


def final_since(day: date) -> datetime:
    """The moment the prices of a trading day become final."""
    return datetime.combine(day + timedelta(days=1), time(FINAL_HOUR))


def due_listings(tickers: list[str], now: datetime | None = None) -> dict[str, date | None]:
    """The listings missing the prices of the latest final day, with their last stored day (None: none stored, the
    whole history is downloaded). Listings whose last price is older than STALE_AFTER_DAYS are no longer expected to
    trade: they are left out.
    """
    if not tickers:
        return {}
    now = now or datetime.now()
    final = latest_final_day(now)
    stale_before = now.date() - timedelta(days=STALE_AFTER_DAYS)
    last = query_last_close_dates(tickers)
    due = {}
    for ticker in tickers:
        stored = last[ticker].date() if ticker in last.index else None
        if stored is None or stale_before <= stored < final:
            due[ticker] = stored
    return due


def update_listing(ticker: str) -> str:
    """Bring the stored Yahoo Finance data of a listing up to date (prices, volumes, distributions; its information if
    unknown); returns what was done (see refresh_history). One update of a listing at a time.
    """
    with key_lock(('update listing', ticker)):
        result = refresh_history(ticker)
        if ticker not in listing_currencies([ticker]):
            refresh_info(ticker)
    return result


def update_listings(tickers: list[str]) -> Counter:
    """Update listings one by one (a failed download is counted, not raised); returns the count of each result."""
    results = Counter()
    for ticker in tickers:
        try:
            results[update_listing(ticker)] += 1
        except Exception as e:  # any download problem: tried again later
            logger.warning('Yahoo Finance update failed for %s: %s', ticker, e)
            results['failed'] += 1
    return results
