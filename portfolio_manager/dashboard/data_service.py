# Single access point of the dashboard to portfolio data.
# Data is cached per account and data version: the version changes whenever an update rewrites the account's
# data files, so the cache never serves stale data and needs no explicit invalidation.
# Cached objects are shared by all browser sessions: callers must treat them as read-only.
import os
import threading
from functools import lru_cache
from typing import NamedTuple

import pandas as pd

from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.backtest.workflows import (
    OPTIMIZATION_SETTINGS_LABEL,
    backtest_portfolio_benchmark,
    backtest_portfolio_optimized,
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
from portfolio_manager.storage.files import load_df_dict_from_excel, to_file_name

# serializes the long-running jobs (data update, optimization)
_UPDATE_LOCK = threading.Lock()


class OptimizedData(NamedTuple):
    hist_data: PortfolioBacktestData
    perf_dct: dict[str, pd.DataFrame] | None  # None if the performance results are missing
    computed_at: float  # modification time of the saved backtest file
    settings: OptimizationSettings  # settings of the saved optimization (defaults for files saved without them)


def _benchmark_name(account: Accounts) -> str:
    return f'{account.name}_benchmark'


def _data_files(account: Accounts) -> list[str]:
    name = to_file_name(account.name)
    return [
        f'{DATA_DIR}/{name}.xlsx',
        f'{DATA_DIR}/{name}_benchmark.xlsx',
        f'{DATA_DIR}/{name}_products_info.xlsx',
        f'{RESULTS_DIR}/{name}.xlsx',
        f'{RESULTS_DIR}/{name}_benchmark.xlsx',
    ]


def data_version(account: Accounts) -> str:
    # latest modification time of the files read by the portfolio page (0 for missing files)
    return str(max(os.path.getmtime(f) if os.path.isfile(f) else 0.0 for f in _data_files(account)))


@lru_cache(maxsize=4)
def _portfolio_data(account_name: str, version: str) -> PortfolioData:
    return PortfolioData(account=Accounts.get_account_by_name(account_name))


@lru_cache(maxsize=4)
def _benchmark_data(account_name: str, version: str) -> PortfolioBacktestData:
    account = Accounts.get_account_by_name(account_name)
    try:
        return load_backtest_data(name=_benchmark_name(account))
    except FileNotFoundError:
        # no saved benchmark yet: compute it from Yahoo Finance data (this also saves it)
        index = get_portfolio_data(account).hist_data.nav_eff.index
        return backtest_portfolio_benchmark(account=account, index=index)


def _catalog_file(account: Accounts) -> str:
    return f'{RESULTS_DIR}/{to_file_name(account.name)}_catalog.xlsx'


@lru_cache(maxsize=2)
def _instruments_data(account_name: str, version: str) -> InstrumentsData:
    return InstrumentsData(account=Accounts.get_account_by_name(account_name))


def get_instruments_data(account: Accounts) -> InstrumentsData | None:
    # None if the account has no ETF catalog
    file = _catalog_file(account)
    if not os.path.isfile(file):
        return None
    return _instruments_data(account.name, str(os.path.getmtime(file)))


def _optimized_files(account: Accounts) -> list[str]:
    name = to_file_name(optimized_portfolio_name(account))
    return [f'{DATA_DIR}/{name}.xlsx', f'{RESULTS_DIR}/{name}.xlsx']


@lru_cache(maxsize=2)
def _optimized_data(account_name: str, version: str) -> OptimizedData:
    account = Accounts.get_account_by_name(account_name)
    name = optimized_portfolio_name(account)
    try:
        perf_dct = load_df_dict_from_excel(file_name=name, folder_name=RESULTS_DIR)
    except FileNotFoundError:
        perf_dct = None
    settings = OptimizationSettings()
    if perf_dct is not None and OPTIMIZATION_SETTINGS_LABEL in perf_dct:
        settings = OptimizationSettings.from_series(perf_dct[OPTIMIZATION_SETTINGS_LABEL].iloc[:, 0])
    return OptimizedData(
        hist_data=load_backtest_data(name=name),
        perf_dct=perf_dct,
        computed_at=os.path.getmtime(_optimized_files(account)[0]),
        settings=settings,
    )


def get_optimized_data(account: Accounts) -> OptimizedData | None:
    # None if no optimization has been computed for the account
    files = _optimized_files(account)
    if not os.path.isfile(files[0]):
        return None
    version = str(max(os.path.getmtime(f) if os.path.isfile(f) else 0.0 for f in files))
    return _optimized_data(account.name, version)


def get_portfolio_data(account: Accounts) -> PortfolioData:
    return _portfolio_data(account.name, data_version(account))


def get_benchmark_data(account: Accounts) -> PortfolioBacktestData:
    return _benchmark_data(account.name, data_version(account))


def update_account(account: Accounts):
    # one update at a time: a second request waits for the running one to finish
    with _UPDATE_LOCK:
        refresh_account(account=account)


def run_optimization(account: Accounts, settings: OptimizationSettings) -> str:
    # backtest of the optimized portfolio over the dates of the saved portfolio (saves its files);
    # returns where the prices come from (online / offline) and the failed optimization dates, if any.
    # Raises ValueError if the settings cannot be satisfied
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
