import logging

from portfolio_manager.analytics.instruments import (
    CATALOG_PERF_LABEL,
    compute_catalog_performance_df,
    load_etf_catalog_data,
)
from portfolio_manager.config.accounts import Accounts
from portfolio_manager.market_data.yahoo import YF_PROD_INFO_LABEL, YFinHistCols

logger = logging.getLogger(__name__)


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
            logger.warning(
                'ETF catalog of %s has no performance metrics: computing them now. '
                'Run compute_catalog_performance or rebuild the catalog to store them.',
                account.name,
            )
            self.perf_df = compute_catalog_performance_df(data)
