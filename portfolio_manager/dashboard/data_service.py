"""Single access point of the dashboard to the saved data, cached per account and data version, and to the long-running
update of an account.
"""

# Data is cached per account and data version: the version changes whenever an update rewrites the account's
# data files, so the cache never serves stale data and needs no explicit invalidation.
# Cached objects are shared by all browser sessions: callers must treat them as read-only.
import os
import threading
from datetime import datetime
from functools import lru_cache

from portfolio_manager.analytics.performance import RESULTS
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.backtest.workflows import (
    BACKTESTS,
    backtest_portfolio_benchmark,
    backtest_saved_at,
    load_backtest_data,
    refresh_account,
)
from portfolio_manager.config.accounts import Account, get_account
from portfolio_manager.config.settings import DATA_DIR, RESULTS_DIR
from portfolio_manager.dashboard.portfolio_data import PortfolioData
from portfolio_manager.market_data.freshness import final_since, latest_final_day
from portfolio_manager.storage.files import to_file_name
from portfolio_manager.storage.tables import derived_folder, tables_version

# tables of the benchmark backtest shown by the dashboard (the others are not loaded)
BENCHMARK_FIELDS = ['nav_eff']

# serializes the updates of the accounts
_UPDATE_LOCK = threading.Lock()


def _benchmark_name(account: Account) -> str:
    return f'{account.name}_benchmark'


def _file_version(path: str) -> float:
    return os.path.getmtime(path) if os.path.isfile(path) else 0.0


def _version(results: list[tuple[str, str, str]]) -> str:
    # latest save of the given derived results (kind, name, folder of their Excel file before the Parquet tables)
    versions = [tables_version(derived_folder(kind, name)) for kind, name, _ in results]
    versions += [_file_version(f'{folder}/{to_file_name(name)}.xlsx') for _, name, folder in results]
    return str(max(versions))


def data_version(account: Account) -> str:
    """Latest save of the data read by the portfolio page (the products are refreshed with the backtest)."""
    name = account.name
    results = [
        (BACKTESTS, name, DATA_DIR),
        (BACKTESTS, _benchmark_name(account), DATA_DIR),
        (RESULTS, name, RESULTS_DIR),
        (RESULTS, _benchmark_name(account), RESULTS_DIR),
    ]
    return _version(results)


@lru_cache(maxsize=4)
def _portfolio_data(account_name: str, version: str) -> PortfolioData:
    return PortfolioData(account=get_account(account_name))


@lru_cache(maxsize=4)
def _benchmark_data(account_name: str, version: str) -> PortfolioBacktestData:
    account = get_account(account_name)
    try:
        return load_backtest_data(name=_benchmark_name(account), fields=BENCHMARK_FIELDS)
    except FileNotFoundError:
        # no saved benchmark yet: compute it from Yahoo Finance data (this also saves it)
        index = get_portfolio_data(account).hist_data.nav_eff.index
        return backtest_portfolio_benchmark(account=account, index=index)


def available_update(account: Account, now: datetime | None = None) -> str | None:
    """Why the account should be updated: the prices of a trading day became final after its last update (None if
    they did not). Compared with the time of the update, not with its last day: after a holiday it is not asked again.
    """
    final_day = latest_final_day(now)
    updated_at = tables_version(derived_folder(BACKTESTS, account.name))  # 0 without a saved backtest
    if updated_at >= final_since(final_day).timestamp():
        return None
    return f'The prices of {final_day:%a %d %b} are final since {final_since(final_day):%a %d %b %H:%M}: press Update'


def missing_data(account: Account | None, update_hint: str) -> str | None:
    """Why the pages of an account cannot be drawn yet (None if they can): no account, account not connected, or no
    saved data (the hint says how to get it).
    """
    if account is None:
        return 'No account yet: add one on the Accounts page'
    if account.currency is None:
        return f'{account.name} is not connected yet: connect it on the Accounts page'
    if backtest_saved_at(account.name) is None:
        return f'No data yet for {account.name}: {update_hint}'
    return None


def get_portfolio_data(account: Account) -> PortfolioData:
    """Portfolio data of an account (cached until its files change)."""
    return _portfolio_data(account.name, data_version(account))


def get_benchmark_data(account: Account) -> PortfolioBacktestData:
    """Benchmark backtest of an account (computed and saved if missing)."""
    return _benchmark_data(account.name, data_version(account))


def update_account(account: Account):
    """One update at a time: a second request waits for the running one to finish."""
    with _UPDATE_LOCK:
        refresh_account(account=account)
