from enum import Enum
from typing import Dict

import pandas as pd

from definitions import RESULTS_DIR, Accounts
from file_utils import save_df_dict_to_excel, load_df_dict_from_excel
from portfolio_history import update_data, compute_hist_portfolio_data
from reporting import compute_portfolio_metrics


class UnitTests(Enum):
    UPDATE_DATA = 1
    COMPUTE_PORTFOLIO_PERFORMANCE = 2
    LOAD_PORTFOLIO_PERFORMANCE = 3
    UPDATE_DATA_NAV_PERFORMANCE = 4


def compute_portfolio_performance(account: Accounts):
    hist_portfolio_data = compute_hist_portfolio_data(account=account)
    results_dict = compute_portfolio_metrics(hist_portfolio_data=hist_portfolio_data)
    save_df_dict_to_excel(df_dict=results_dict,
                          folder_name=RESULTS_DIR,
                          file_name=account.name)


def load_portfolio_performance(account: Accounts) -> Dict[str, pd.DataFrame]:
    return load_df_dict_from_excel(folder_name=RESULTS_DIR, file_name=account.name)


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.UPDATE_DATA:
        for account in Accounts:
            update_data(account=account)
    elif unit_test == UnitTests.COMPUTE_PORTFOLIO_PERFORMANCE:
        for account in Accounts:
            compute_portfolio_performance(account=account)
    elif unit_test == UnitTests.LOAD_PORTFOLIO_PERFORMANCE:
        print(load_portfolio_performance(account=Accounts.CHF))
    elif unit_test == UnitTests.UPDATE_DATA_NAV_PERFORMANCE:
        for account in Accounts:
            update_data(account=account)
            compute_hist_portfolio_data(account=account)
            compute_portfolio_performance(account=account)
    else:
        raise NotImplementedError


if __name__ == '__main__':
    unit_test = UnitTests.UPDATE_DATA_NAV_PERFORMANCE
    run_unit_test(unit_test=unit_test)
