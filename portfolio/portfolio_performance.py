from enum import Enum
from typing import Dict

import pandas as pd

from config.definitions import RESULTS_DIR
from config.accounts import Accounts
from utils.file_utils import save_df_dict_to_excel, load_df_dict_from_excel
from engines.reporting import compute_portfolio_metrics, PerfDataTabs


class UnitTests(Enum):
    UPDATE_DATA = 1
    COMPUTE_PORTFOLIO_PERFORMANCE = 2
    LOAD_PORTFOLIO_PERFORMANCE = 3
    UPDATE_DATA_COMPUTE_PERFORMANCE = 4


def compute_portfolio_performance(account: Accounts):
    results_dict = compute_portfolio_metrics(hist_portfolio_data=hist_portfolio_data,
                                             strategy_benchmark=hist_benchmark_data.nav)
    results_bm_dict = compute_portfolio_metrics(nav=hist_benchmark_data.nav_eff)
    save_performance_data(results_dict=results_dict,
                          file_name=account.name)
    save_performance_data(results_dict=results_bm_dict,
                          file_name=f'{account.name}_benchmark')


def load_performance_data_portfolio(account: Accounts) -> Dict[str, pd.DataFrame]:
    return load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=account.name)


def load_performance_data_benchmark(account: Accounts) -> Dict[str, pd.DataFrame]:
    try:
        return load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=f'{account.name}_benchmark')
    except FileNotFoundError:
        return {PerfDataTabs.RISK_METRICS: pd.DataFrame()}


def save_performance_data(results_dict: Dict[str, pd.DataFrame], file_name: str):
    save_df_dict_to_excel(df_dict=results_dict, folder_name=RESULTS_DIR, file_name=file_name)


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.UPDATE_DATA:
        for account in Accounts:
            update_data(account=account)
    elif unit_test == UnitTests.COMPUTE_PORTFOLIO_PERFORMANCE:
        for account in Accounts:
            compute_portfolio_performance(account=account)
    elif unit_test == UnitTests.LOAD_PORTFOLIO_PERFORMANCE:
        print(load_performance_data_portfolio(account=Accounts.DEGIRO_CHF))
    elif unit_test == UnitTests.UPDATE_DATA_COMPUTE_PERFORMANCE:
        for account in Accounts:
            update_data(account=account)
            compute_portfolio_performance(account=account)
    else:
        raise NotImplementedError


if __name__ == '__main__':
    unit_test = UnitTests.UPDATE_DATA_COMPUTE_PERFORMANCE
    run_unit_test(unit_test=unit_test)
