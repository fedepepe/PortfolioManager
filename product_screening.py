from enum import Enum

from sql import query_products, query_close
from product_definitions import ProductTypes, Exchanges, Currencies
from charts import fetch_charts, save_charts


class UnitTests(Enum):
	REPORT_TOP_ETF_RETURNS = 1
	LOAD_ETF_PRICES = 2
	LOAD_SINGLE_ETF_PRICE_FX = 3


def run_unit_test(unit_test: UnitTests):
	if unit_test == UnitTests.REPORT_TOP_ETF_RETURNS:
		product_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)
		close_df = fetch_charts(product_info_df=product_info_df, rename_columns_to='ids')
		# returns = close_df.pct_change(30).iloc[-1, :]
	elif unit_test == UnitTests.LOAD_ETF_PRICES:
		close_df = query_close(product_id=850550)
		pass
	elif unit_test == UnitTests.LOAD_SINGLE_ETF_PRICE_FX:
		base_curr = Currencies.CHF
		product_info_df = query_products(product_isin='IE00B1TXHL60', tradable=True, exchange=Exchanges.SWX)
		close_df = fetch_charts(product_info_df=product_info_df)
		fx_info_df = query_products(product_type='CURRENCY')
		fx_info_df = fx_info_df.loc[fx_info_df['name'] == f"{product_info_df.iloc[0]['currency']}/{base_curr}", :]
		fx_rates_df = fetch_charts(product_info_df=fx_info_df)
		print(close_df.iloc[:, 0].mul(fx_rates_df.iloc[:, 0]))
	else:
		raise NotImplementedError


if __name__ == '__main__':
	unit_test = UnitTests.LOAD_SINGLE_ETF_PRICE_FX
	run_unit_test(unit_test=unit_test)
