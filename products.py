import logging
from typing import List, Optional

import pandas as pd

import file_utils as fu
from definitions import DATA_DIR
from degiro_connection import TRADING_API
from sql import insert_product, get_products, get_max_product_id
from transactions import load_tx_history

logging.basicConfig(level=logging.DEBUG)


def fetch_full_product_catalog():
	# FETCH PRODUCT INFO
	n_start = get_max_product_id()
	if n_start is None:
		n_start = 0
	n = n_start
	while True:
		product_info = (TRADING_API
		                .get_products_info(product_list=[i for i in range(n, n + 1000)], raw=False, ))
		if hasattr(product_info, 'data'):
			for prod_id in product_info.data:
				if product_info.data[prod_id].product_type in ['STOCK', 'ETF', 'BOND', 'CURRENCY']:
					insert_product(product_info.data[prod_id])
		if n > n_start + 10e6:
			break
		n += 1000
	return product_info


def fetch_single_product(product_id: int):
	product_info = TRADING_API.get_products_info(product_list=[product_id], raw=False)
	if hasattr(product_info, 'data'):
		insert_product(product_info.data[product_id])


def read_product_catalog(product_name: Optional[str] = None,
                         product_id: Optional[int] = None,
                         product_type: Optional[str] = None,
                         ) -> pd.DataFrame:
	return get_products(product_name=product_name, product_id=product_id, product_type=product_type)


def fetch_product_info(product_ids: int | List[int] = 11853206):
	if isinstance(product_ids, int):
		product_ids = [product_ids]
	# FETCH PRODUCT INFO
	product_info = TRADING_API.get_products_info(
		product_list=product_ids,
		raw=False,
	)
	# return as dataframe
	product_info_df = pd.DataFrame.from_dict({k: v.__dict__ for k, v in product_info.data.items()}, orient='index')
	return product_info_df


def save_product_info(product_info_df: pd.DataFrame):
	try:
		df_old = fu.load_df_from_excel(file_name='products_info', folder=DATA_DIR)
	except FileNotFoundError:
		df_old = pd.DataFrame()
	df = pd.concat([df_old, product_info_df], axis=1)
	df = df.loc[:, ~df.columns[::-1].duplicated()[::-1]]
	fu.save_df_to_excel(df=df, file_name='products_info', folder=DATA_DIR)


def fetch_portfolio_products():
	tx_history_df = load_tx_history()
	product_ids = list(set(tx_history_df['product_id'].to_list()))
	product_df = fetch_product_info(product_ids=product_ids)
	save_product_info(product_df)


def load_portfolio_products() -> pd.DataFrame:
	product_df = fu.load_df_from_excel(file_name='products_info', folder=DATA_DIR)
	return product_df


if __name__ == '__main__':
	# print(read_product_catalog(product_type='CURRENCY'))
	fetch_full_product_catalog()
	# fetch_single_product(product_id=24739339)
	# print(read_product_catalog(product_id=24739339))
