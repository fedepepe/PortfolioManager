import logging

from config.accounts import Accounts
from portfolio.instruments_performance import load_etf_catalog_data, compute_catalog_performance_df, CATALOG_PERF_LABEL
from yahoo_finance.yahoo_finance import YFinHistCols, YF_PROD_INFO_LABEL


# read-only view of the saved ETF catalog of an account, shared through dashboard.data_service: do not modify
class InstrumentsData:
    def __init__(self, account: Accounts):
        self.account = account
        data = load_etf_catalog_data(account=account)
        self.adj_close_df = data[YFinHistCols.adj_close]
        self.volume_df = data[YFinHistCols.volume]
        self.prod_info_df = data[YF_PROD_INFO_LABEL]
        if CATALOG_PERF_LABEL in data:
            self.perf_df = data[CATALOG_PERF_LABEL]
        else:
            # catalog saved before the performance metrics were stored with it: compute them in memory only
            logging.warning(
                f'ETF catalog of {account.name} has no performance metrics: computing them now. '
                f'Run compute_catalog_performance or rebuild the catalog to store them.'
            )
            self.perf_df = compute_catalog_performance_df(data)
