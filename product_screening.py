import pandas as pd

from product_definitions import ProductTypes, Exchanges
from sql import query_products


if __name__ == '__main__':
	etf_info_df = pd.DataFrame()
	for exc in Exchanges:
		df = query_products(product_type=ProductTypes.ETF, tradable=True, exchange=exc.value)
		etf_info_df = pd.concat([etf_info_df, df])
	pass
