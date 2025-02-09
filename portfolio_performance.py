from enum import Enum

from portfolio_history import update_data, compute_hist_portfolio_data
from reporting import compute_portfolio_metrics
from file_utils import save_df_dict_to_excel
from definitions import RESULTS_DIR, PORTFOLIO_NAME


class UnitTests(Enum):
    UPDATE_DATA = 1
    COMPUTE_PORTFOLIO_PERFORMANCE = 2


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.UPDATE_DATA:
        update_data()
    elif unit_test == UnitTests.COMPUTE_PORTFOLIO_PERFORMANCE:
        hist_portfolio_data = compute_hist_portfolio_data()
        results_dict = compute_portfolio_metrics(hist_portfolio_data=hist_portfolio_data)
        save_df_dict_to_excel(df_dict=results_dict,
                              folder=RESULTS_DIR,
                              file_name=PORTFOLIO_NAME)


if __name__ == '__main__':
    unit_test = UnitTests.COMPUTE_PORTFOLIO_PERFORMANCE
    run_unit_test(unit_test=unit_test)
