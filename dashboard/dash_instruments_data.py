from config.accounts import Accounts
from portfolio.instruments_performance import load_etf_catalog_data, compute_product_performance
from yahoo_finance.yahoo_finance import YFinHistCols, YF_PROD_INFO_LABEL


class InstrumentsData:
	def __init__(self, account: Accounts):
		self.account = account
		data = load_etf_catalog_data(account=account)
		self.adj_close_df = data[YFinHistCols.adj_close]
		self.volume_df = data[YFinHistCols.volume]
		self.prod_info_df = data[YF_PROD_INFO_LABEL]
		self.perf_df = compute_product_performance(adj_close_df=self.adj_close_df,
		                                           volume_df=self.volume_df,
		                                           prod_info_df=self.prod_info_df)

	def update(self):
		self.__init__(account=self.account)
