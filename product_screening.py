from enum import Enum

from sql import query_products, query_close
from product_definitions import ProductTypes
from charts import fetch_charts, save_charts


class UnitTests(Enum):
	REPORT_TOP_ETF_RETURNS = 1
	LOAD_ETF_PRICES = 2


def run_unit_test(unit_test: UnitTests):
	if unit_test == UnitTests.REPORT_TOP_ETF_RETURNS:
		product_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)
		close_df = fetch_charts(product_info_df=product_info_df, rename_columns_to='ids')
		# returns = close_df.pct_change(30).iloc[-1, :]
	elif unit_test == UnitTests.LOAD_ETF_PRICES:
		close_df = query_close(product_id=850550)
		pass
	else:
		raise NotImplementedError


if __name__ == '__main__':
	unit_test = UnitTests.LOAD_ETF_PRICES
	run_unit_test(unit_test=unit_test)
