"""Background update of the Yahoo Finance data (prices, volumes, distributions) of the listings the dashboard uses,
while the dashboard server runs.

At every pass (every CHECK_EVERY), the listings missing the prices of the latest final trading day (see
market_data.freshness) are updated one by one, in order of priority: the instruments of the saved allocations, of the
benchmarks and of the portfolios first, then those of the Tactical Asset Allocation research (config.taa), then the
catalog from the most traded listing. A listing whose download brought
nothing new (Yahoo Finance has not published the day yet, or a holiday) is tried again after RETRY_AFTER. After a pass
that stored new data, the candidates computed today for the allocations are computed again at their next view.

The interest rates of the research (market_data.rates) are updated once a day, from FINAL_HOUR (again after RETRY_AFTER
when a download failed).

Degiro data is not downloaded here (Update button of the Portfolio page).
"""

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from portfolio_manager.analytics.instruments import (
    average_traded_values,
    catalog_products,
    rank_by_traded_value,
)
from portfolio_manager.analytics.saa import clear_day_cache
from portfolio_manager.config.accounts import list_accounts
from portfolio_manager.config.taa import TAA_INSTRUMENTS
from portfolio_manager.market_data.freshness import FINAL_HOUR, due_listings, latest_final_day, update_listing
from portfolio_manager.market_data.rates import update_rates
from portfolio_manager.market_data.yahoo import is_yahoo_reachable
from portfolio_manager.storage.queries import (
    list_allocations,
    query_account_product_ids,
    query_allocation,
    query_benchmark,
    query_degiro_yahoo_map,
)

logger = logging.getLogger(__name__)

CHECK_EVERY = 15 * 60  # seconds between two passes
RETRY_AFTER = timedelta(hours=3)  # a listing whose download brought nothing new is tried again after this
PAUSE = 0.2  # seconds between two downloads (spread the requests to Yahoo Finance)


@dataclass(frozen=True)
class UpdateStatus:
    """State of the background update: running or not, progress of the current pass, end of the last pass, how many
    listings it updated and how many still miss the latest final day (tried again later).
    """

    running: bool = False
    done: int = 0
    total: int = 0
    finished_at: datetime | None = None
    updated: int = 0
    missing: int = 0
    final_day: object = None  # the latest final trading day of the last pass


def priority_tickers() -> list[str]:
    """The listings to keep up to date, in order of priority (no duplicates): the instruments of the saved allocations,
    of the benchmarks and of the portfolios of every account, then those of the Tactical Asset Allocation research,
    then the catalog from the most traded listing.
    """
    accounts = list_accounts()
    first = []
    for account in accounts:
        for name in list_allocations(account.name):
            allocation = query_allocation(account.name, name)
            first += [i.ticker for i in allocation.instruments] if allocation is not None else []
        benchmark = query_benchmark(account.name)
        first += [c.ticker for c in benchmark.components if c.ticker] if benchmark is not None else []
        first += list(query_degiro_yahoo_map(query_account_product_ids(account.name)).values())
    first += [instrument.ticker for instrument in TAA_INSTRUMENTS]
    catalog = sorted(set(query_degiro_yahoo_map(catalog_products()['id'].to_list()).values()))
    averages = average_traded_values(catalog)
    connected = [a for a in accounts if a.currency is not None]
    if connected and not averages.empty:  # traded values compared at the latest exchange rates
        ranked = rank_by_traded_value(connected[0], averages)
    else:
        ranked = averages.sort_values('traded_value', ascending=False).index.to_list()
    rest = [t for t in catalog if t not in set(ranked)]  # without recent prices: last
    return list(dict.fromkeys(first + ranked + rest))


class BackgroundUpdater:
    """Updates the listings in a thread of the dashboard server (see the module)."""

    def __init__(self):
        self.status = UpdateStatus()
        self._attempts: dict[str, datetime] = {}  # last download that brought nothing new, by listing
        self._rates_checked: datetime | None = None  # last update of the interest rates
        self._rates_failed = False  # a series of the last update was not downloaded
        self._lock = threading.Lock()  # one pass at a time
        self._thread: threading.Thread | None = None

    def start(self):
        """Start the passes in the background (once per process)."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name='price-updates', daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            try:
                self.run_pass()
            except Exception:  # never stops the loop: the next pass tries again
                logger.exception('Background update of the prices failed')
            try:
                self.update_rates()
            except Exception:
                logger.exception('Background update of the interest rates failed')
            time.sleep(CHECK_EVERY)

    def run_pass(self, now: datetime | None = None) -> int:
        """Update the listings missing the latest final day (not those tried recently); returns how many got new
        data.
        """
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            if not is_yahoo_reachable():
                logger.info('Yahoo Finance not reachable: prices not updated')
                return 0
            now = now or datetime.now()
            tickers = priority_tickers()
            due = due_listings(tickers, now)
            todo = [t for t in tickers if t in due and now - self._attempts.get(t, datetime.min) >= RETRY_AFTER]
            self.status = replace(self.status, running=True, done=0, total=len(todo))
            for n, ticker in enumerate(todo):
                try:
                    update_listing(ticker)
                except Exception as e:  # any download problem: tried again later
                    logger.warning('Yahoo Finance update failed for %s: %s', ticker, e)
                self._attempts[ticker] = now
                self.status = replace(self.status, done=n + 1)
                time.sleep(PAUSE)
            still_due = due_listings(tickers, now)
            missing = len(still_due)
            updated = sum(t not in still_due for t in todo)  # the listings that got the latest final day
            self._attempts = {t: at for t, at in self._attempts.items() if t in still_due}  # forget the up-to-date
            if updated:
                clear_day_cache()
            self.status = UpdateStatus(
                finished_at=datetime.now(), updated=updated, missing=missing, final_day=latest_final_day(now)
            )
            if todo:
                logger.info(
                    'Prices updated in the background: %d of %d listings, %d still missing', updated, len(todo), missing
                )
            return updated
        finally:
            if self.status.running:
                self.status = replace(self.status, running=False)
            self._lock.release()

    def update_rates(self, now: datetime | None = None) -> bool:
        """Update the interest rates once a day from FINAL_HOUR (again after RETRY_AFTER if a download failed); returns
        whether they were updated.
        """
        now = now or datetime.now()
        checked = self._rates_checked
        if now.hour < FINAL_HOUR or (
            checked is not None
            and checked.date() == now.date()
            and (not self._rates_failed or now - checked < RETRY_AFTER)
        ):
            return False
        results = update_rates()
        self._rates_checked, self._rates_failed = now, bool(results.get('failed'))
        return True


UPDATER = BackgroundUpdater()  # the one of the dashboard server


def start_background_updates():
    """Start the background update of the prices (dashboard server start-up: nothing is downloaded before it)."""
    UPDATER.start()


def status_text(status: UpdateStatus) -> str:
    """The state of the background update, for the dashboard."""
    if status.running:
        return f'Updating prices in the background: {status.done:,} / {status.total:,} listings'
    if status.finished_at is None:
        return 'Prices: background update not run yet'
    text = f'Prices up to date with {status.final_day:%a %d %b} (checked {status.finished_at:%H:%M})'
    if status.missing:
        text += f'; {status.missing:,} listings without it yet, tried again later'
    return text
