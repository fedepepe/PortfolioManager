"""Performance results of the portfolios and benchmarks (Excel files of the results folder)."""

import pandas as pd

from portfolio_manager.analytics.metrics import PerfDataTabs, compute_portfolio_metrics
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.config.accounts import Accounts
from portfolio_manager.config.settings import RESULTS_DIR
from portfolio_manager.storage.files import load_df_dict_from_excel, save_df_dict_to_excel


def compute_portfolio_performance(
    account: Accounts, hist_portfolio_data: PortfolioBacktestData, hist_benchmark_data: PortfolioBacktestData
):
    """Compute and save the performance of an account and of its benchmark."""
    results_dict = compute_portfolio_metrics(
        hist_portfolio_data=hist_portfolio_data, strategy_benchmark=hist_benchmark_data.nav
    )
    results_bm_dict = compute_portfolio_metrics(nav=hist_benchmark_data.nav_eff)
    save_performance_data(results_dict=results_dict, file_name=account.name)
    save_performance_data(results_dict=results_bm_dict, file_name=f'{account.name}_benchmark')


def load_performance_data_portfolio(account: Accounts) -> dict[str, pd.DataFrame]:
    """Saved performance of an account."""
    return load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=account.name)


def load_performance_data_benchmark(account: Accounts) -> dict[str, pd.DataFrame]:
    """Saved performance of the benchmark of an account (empty if missing)."""
    try:
        return load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=f'{account.name}_benchmark')
    except FileNotFoundError:
        return {PerfDataTabs.RISK_METRICS: pd.DataFrame()}


def save_performance_data(results_dict: dict[str, pd.DataFrame], file_name: str):
    """Save performance results."""
    save_df_dict_to_excel(df_dict=results_dict, folder_name=RESULTS_DIR, file_name=file_name)
