# Single access point of the dashboard to portfolio data.
# Data is cached per account and data version: the version changes whenever an update rewrites the account's
# data files, so the cache never serves stale data and needs no explicit invalidation.
# Cached objects are shared by all browser sessions: callers must treat them as read-only.
import os
import threading
from functools import lru_cache

from config.accounts import Accounts
from config.definitions import DATA_DIR, RESULTS_DIR
from dashboard.dash_portfolio_data import PortfolioData
from portfolio.portfolio_backtest import load_backtest_data, backtest_portfolio_benchmark, refresh_account
from portfolio.portfolio_definitions import PortfolioBacktestData
from utils.file_utils import to_file_name

_UPDATE_LOCK = threading.Lock()


def _benchmark_name(account: Accounts) -> str:
    return f'{account.name}_benchmark'


def _data_files(account: Accounts) -> list[str]:
    name = to_file_name(account.name)
    return [f'{DATA_DIR}/{name}.xlsx',
            f'{DATA_DIR}/{name}_benchmark.xlsx',
            f'{DATA_DIR}/{name}_products_info.xlsx',
            f'{RESULTS_DIR}/{name}.xlsx',
            f'{RESULTS_DIR}/{name}_benchmark.xlsx']


def data_version(account: Accounts) -> str:
    # latest modification time of the files read by the portfolio page (0 for missing files)
    return str(max(os.path.getmtime(f) if os.path.isfile(f) else 0. for f in _data_files(account)))


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


def get_portfolio_data(account: Accounts) -> PortfolioData:
    return _portfolio_data(account.name, data_version(account))


def get_benchmark_data(account: Accounts) -> PortfolioBacktestData:
    return _benchmark_data(account.name, data_version(account))


def update_account(account: Accounts):
    # one update at a time: a second request waits for the running one to finish
    with _UPDATE_LOCK:
        refresh_account(account=account)
