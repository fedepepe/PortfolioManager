"""Data of the Instruments page."""

from portfolio_manager.analytics.instruments import InstrPerfTableCols, catalog_table, stored_adjusted_prices
from portfolio_manager.config.accounts import Account


# read-only view of the ETF catalog of an account, shared through dashboard.data_service: do not modify
class InstrumentsData:
    """ETF catalog of an account: the table of the ETFs (by rank) and their adjusted prices in the account currency."""

    def __init__(self, account: Account):
        self.account = account
        self.perf_df = catalog_table(account)
        self.adj_close_df = stored_adjusted_prices(account, self.perf_df[InstrPerfTableCols.ticker].to_list())
