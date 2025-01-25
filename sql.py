from sqlite3 import IntegrityError
from typing import Optional

import pandas as pd
from degiro_connector.trading.models.product import ProductItem
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from db_conn import engine, conn
from sql_utils import list_to_str, str_to_date
from table_definitions import Product, Close
from product_definitions import Exchanges


def insert_product(product: ProductItem):
	data = Product(active=product.active,
	               buy_order_types=list_to_str(product.buy_order_types),
	               category=product.category,
	               close_price=product.close_price,
	               close_price_date=str_to_date(product.close_price_date),
	               contract_size=product.contract_size,
	               currency=product.currency,
	               exchange_id=product.exchange_id,
	               feed_quality=product.feed_quality,
	               feed_quality_secondary=product.feed_quality_secondary,
	               id=product.id,
	               is_shortable=product.is_shortable,
	               isin=product.isin,
	               name=product.name,
	               only_eod_prices=product.only_eod_prices,
	               order_book_depth=product.order_book_depth,
	               order_book_depth_secondary=product.order_book_depth_secondary,
	               order_time_types=list_to_str(product.order_time_types),
	               product_bit_types=list_to_str(product.product_bit_types),
	               product_type=product.product_type,
	               product_type_id=product.product_type_id,
	               quality_switch_free=product.quality_switch_free,
	               quality_switch_free_secondary=product.quality_switch_free_secondary,
	               quality_switchable=product.quality_switchable,
	               quality_switchable_secondary=product.quality_switchable_secondary,
	               sell_order_types=list_to_str(product.sell_order_types),
	               strike_price=product.strike_price,
	               symbol=product.symbol,
	               tradable=product.tradable,
	               vwd_id=product.vwd_id,
	               vwd_id_secondary=product.vwd_id_secondary,
	               vwd_identifier_type=product.vwd_identifier_type,
	               vwd_identifier_type_secondary=product.vwd_identifier_type_secondary,
	               vwd_module_id=product.vwd_module_id,
	               vwd_module_id_secondary=product.vwd_module_id_secondary,
	               )
	conn.add(data)
	print([k for k, v in product.dict().items() if isinstance(v, list)])
	db_commit(message=f'{product.id} - {product.name}')


def insert_close(series: pd.Series):
	for date, close in series.items():
		data = Close(product_id=series.name,
		             date=date,
		             close=close)
		conn.add(data)
	db_commit(message=f'Added closing prices of product {series.name}')


def db_commit(message: Optional[str] = None):
	try:
		conn.commit()
		if message is not None:
			print(f'Added entry {message}.')
	except (IntegrityError, IntegrityError) as e:
		if 'UNIQUE constraint failed' in e._message():
			if message is not None:
				print(f'Entry {message} already exists. Skipped.')
		else:
			raise e
	except PendingRollbackError:
		conn.rollback()


def query_products(product_name: Optional[str] = None,
                   product_id: Optional[int] = None,
                   product_isin: Optional[str] = None,
                   product_type: Optional[str] = None,
                   tradable: Optional[bool] = None,
                   exchange: Optional[Exchanges] = None,
                   ) -> pd.DataFrame:
	stmt = select(Product)
	if product_name is not None:
		cond = Product.name == product_name
	elif product_id is not None:
		cond = Product.id == product_id
	elif product_isin is not None:
		cond = Product.isin == product_isin
	elif product_type is not None:
		cond = Product.product_type == product_type
	else:
		raise Exception('Product name, id, isin or type must be specified.')
	if tradable:
		cond = cond & (Product.tradable == tradable)
	if exchange is not None:
		cond = cond & (Product.exchange_id == exchange)
	stmt = stmt.where(cond)
	df = pd.read_sql(stmt, engine)
	df = df.set_index('id', drop=False)
	return df


def query_close(product_id: int,
                date_start: Optional[pd.Timestamp] = None,
                date_stop: Optional[pd.Timestamp] = None):
	cond = Close.product_id == product_id
	if date_start is not None:
		cond = cond & (Close.date >= date_start)
	if date_stop is not None:
		cond = cond & (Close.date < date_stop)
	stmt = select(Close).where(cond)
	df = pd.read_sql(stmt, engine)
	df = df.set_index('date', drop=False)
	df = df.pivot(columns='product_id', index='date', values='close')
	df.index = pd.to_datetime(df.index)
	return df


def get_product_types() -> float:
	return conn.query(Product.product_type).distinct().all()


def get_max_product_id() -> int:
	return conn.query(func.max(Product.id)).all()[0][0]
