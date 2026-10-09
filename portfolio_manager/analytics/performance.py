"""Performance results of the portfolios and benchmarks: Parquet tables (working copy) and Excel files of the results
folder (for inspection).
"""

import time

import pandas as pd

from portfolio_manager.analytics.metrics import PerfDataTabs, compute_portfolio_metrics
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.config.accounts import Account
from portfolio_manager.config.settings import RESULTS_DIR
from portfolio_manager.storage.files import load_df_dict_from_excel, save_df_dict_to_excel
from portfolio_manager.storage.tables import derived_folder, has_tables, load_tables, save_tables

RESULTS = 'results'  # kind of derived result (folder of the Parquet tables)


def compute_portfolio_performance(
    account: Account, hist_portfolio_data: PortfolioBacktestData, hist_benchmark_data: PortfolioBacktestData
):
    """Compute and save the performance of an account and of its benchmark."""
    results_dict = compute_portfolio_metrics(
        hist_portfolio_data=hist_portfolio_data, strategy_benchmark=hist_benchmark_data.nav
    )
    results_bm_dict = compute_portfolio_metrics(nav=hist_benchmark_data.nav_eff)
    save_performance_data(results_dict=results_dict, file_name=account.name)
    save_performance_data(results_dict=results_bm_dict, file_name=f'{account.name}_benchmark')


def load_performance_data(file_name: str) -> dict[str, pd.DataFrame]:
    """Saved performance results, as tables (series become one-column tables, as in the Excel files)."""
    folder = derived_folder(RESULTS, file_name)
    if not has_tables(folder):
        return load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=file_name)  # saved before the Parquet tables
    return {k: v.to_frame() if isinstance(v, pd.Series) else v for k, v in load_tables(folder).items()}


def load_performance_data_portfolio(account: Account) -> dict[str, pd.DataFrame]:
    """Saved performance of an account."""
    return load_performance_data(account.name)


def load_performance_data_benchmark(account: Account) -> dict[str, pd.DataFrame]:
    """Saved performance of the benchmark of an account (empty if missing)."""
    try:
        return load_performance_data(f'{account.name}_benchmark')
    except FileNotFoundError:
        return {PerfDataTabs.RISK_METRICS: pd.DataFrame()}


def save_performance_data(results_dict: dict[str, pd.DataFrame], file_name: str, saved_at: float | None = None):
    """Save performance results: Parquet tables and Excel file; saved_at (default: now) is the computation time."""
    save_df_dict_to_excel(df_dict=results_dict, folder_name=RESULTS_DIR, file_name=file_name)
    save_performance_tables(results_dict, file_name, saved_at=saved_at)


def save_performance_tables(results_dict: dict[str, pd.DataFrame], file_name: str, saved_at: float | None = None):
    """Save the Parquet tables of performance results."""
    attributes = {'saved_at': saved_at if saved_at is not None else time.time()}
    save_tables(derived_folder(RESULTS, file_name), results_dict, attributes=attributes)
