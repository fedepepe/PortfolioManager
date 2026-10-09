"""Single access point of the dashboard to the saved data, cached per account and data version, and to the long-running
jobs (update, optimization).
"""

# Data is cached per account and data version: the version changes whenever an update rewrites the account's
# data files, so the cache never serves stale data and needs no explicit invalidation.
# Cached objects are shared by all browser sessions: callers must treat them as read-only.
import os
import threading
from functools import lru_cache
from typing import NamedTuple

import pandas as pd

from portfolio_manager.analytics.instruments import CATALOGS, catalog_file_name
from portfolio_manager.analytics.performance import RESULTS, load_performance_data
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.backtest.workflows import (
    BACKTESTS,
    OPTIMIZATION_SETTINGS_LABEL,
    backtest_portfolio_benchmark,
    backtest_portfolio_optimized,
    backtest_saved_at,
    load_backtest_data,
    optimization_prices,
    optimized_portfolio_name,
    refresh_account,
)
from portfolio_manager.config.accounts import Accounts
from portfolio_manager.config.settings import DATA_DIR, RESULTS_DIR
from portfolio_manager.dashboard.instruments_data import InstrumentsData
from portfolio_manager.dashboard.portfolio_data import PortfolioData
from portfolio_manager.optimization.settings import OptimizationSettings
from portfolio_manager.storage.files import to_file_name
from portfolio_manager.storage.tables import derived_folder, tables_version

# tables of the benchmark and optimized backtests shown by the dashboard (the others are not loaded)
BENCHMARK_FIELDS = ['nav_eff']
OPTIMIZED_FIELDS = ['nav_eff']

# serializes the long-running jobs (data update, optimization)
_UPDATE_LOCK = threading.Lock()


class OptimizedData(NamedTuple):
    """Saved optimization of an account: backtest, performance, date and settings."""

    hist_data: PortfolioBacktestData
    perf_dct: dict[str, pd.DataFrame] | None  # None if the performance results are missing
    computed_at: float  # time the saved optimization was computed
    settings: OptimizationSettings  # settings of the saved optimization (defaults for files saved without them)


def _benchmark_name(account: Accounts) -> str:
    return f'{account.name}_benchmark'


def _file_version(path: str) -> float:
    return os.path.getmtime(path) if os.path.isfile(path) else 0.0


def _version(results: list[tuple[str, str, str]]) -> str:
    # latest save of the given derived results (kind, name, folder of their Excel file before the Parquet tables)
    versions = [tables_version(derived_folder(kind, name)) for kind, name, _ in results]
    versions += [_file_version(f'{folder}/{to_file_name(name)}.xlsx') for _, name, folder in results]
    return str(max(versions))


def data_version(account: Accounts) -> str:
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
    return PortfolioData(account=Accounts.get_account_by_name(account_name))


@lru_cache(maxsize=4)
def _benchmark_data(account_name: str, version: str) -> PortfolioBacktestData:
    account = Accounts.get_account_by_name(account_name)
    try:
        return load_backtest_data(name=_benchmark_name(account), fields=BENCHMARK_FIELDS)
    except FileNotFoundError:
        # no saved benchmark yet: compute it from Yahoo Finance data (this also saves it)
        index = get_portfolio_data(account).hist_data.nav_eff.index
        return backtest_portfolio_benchmark(account=account, index=index)


def _catalog_version(account: Accounts) -> str:
    return _version([(CATALOGS, catalog_file_name(account), RESULTS_DIR)])


@lru_cache(maxsize=2)
def _instruments_data(account_name: str, version: str) -> InstrumentsData:
    return InstrumentsData(account=Accounts.get_account_by_name(account_name))


def get_instruments_data(account: Accounts) -> InstrumentsData | None:
    """None if the account has no ETF catalog."""
    version = _catalog_version(account)
    if float(version) == 0.0:
        return None
    return _instruments_data(account.name, version)


def _optimized_version(account: Accounts) -> str:
    name = optimized_portfolio_name(account)
    return _version([(BACKTESTS, name, DATA_DIR), (RESULTS, name, RESULTS_DIR)])


@lru_cache(maxsize=2)
def _optimized_data(account_name: str, version: str) -> OptimizedData:
    account = Accounts.get_account_by_name(account_name)
    name = optimized_portfolio_name(account)
    try:
        perf_dct = load_performance_data(name)
    except FileNotFoundError:
        perf_dct = None
    settings = OptimizationSettings()
    if perf_dct is not None and OPTIMIZATION_SETTINGS_LABEL in perf_dct:
        settings = OptimizationSettings.from_series(perf_dct[OPTIMIZATION_SETTINGS_LABEL].iloc[:, 0])
    return OptimizedData(
        hist_data=load_backtest_data(name=name, fields=OPTIMIZED_FIELDS),
        perf_dct=perf_dct,
        computed_at=backtest_saved_at(name),
        settings=settings,
    )


def get_optimized_data(account: Accounts) -> OptimizedData | None:
    """None if no optimization has been computed for the account."""
    if backtest_saved_at(optimized_portfolio_name(account)) is None:
        return None
    return _optimized_data(account.name, _optimized_version(account))


def get_portfolio_data(account: Accounts) -> PortfolioData:
    """Portfolio data of an account (cached until its files change)."""
    return _portfolio_data(account.name, data_version(account))


def get_benchmark_data(account: Accounts) -> PortfolioBacktestData:
    """Benchmark backtest of an account (computed and saved if missing)."""
    return _benchmark_data(account.name, data_version(account))


def update_account(account: Accounts):
    """One update at a time: a second request waits for the running one to finish."""
    with _UPDATE_LOCK:
        refresh_account(account=account)


def run_optimization(account: Accounts, settings: OptimizationSettings) -> str:
    """Backtest of the optimized portfolio over the dates of the saved portfolio (saves its files); returns where the
    prices come from (online / offline) and the failed optimization dates, if any. Raises ValueError if the settings
    cannot be satisfied.
    """
    error = settings.validate()
    if error:
        raise ValueError(error)
    with _UPDATE_LOCK:
        index = get_portfolio_data(account).hist_data.nav_eff.index
        prices_df, prices_summary = optimization_prices(account=account)
        error = settings.validate(n_assets=prices_df.shape[1])
        if error:
            raise ValueError(error)
        _, optimized_weights = backtest_portfolio_optimized(
            account=account, index=index, prices_adj_df=prices_df, settings=settings
        )
    summary = f'prices {prices_summary}'
    n_failed = len(optimized_weights.failed_dates)
    if n_failed:
        summary += (
            f'; optimization failed on {n_failed} of {len(optimized_weights.weights)} dates, previous weights kept'
        )
    return summary
