from sqlite3 import IntegrityError
from typing import Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from db_conn import engine, conn
from sql_utils import list_to_str, str_to_date
from table_definitions import Product


def insert_product(product):
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


# db.refresh(data)


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


def get_product(product_name: Optional[str] = None,
                product_type: Optional[str] = None
                ) -> pd.DataFrame:
	stmt = select(Product)
	if product_name is not None:
		stmt = stmt.where(Product.name == product_name)
	if product_type is not None:
		stmt = stmt.where(Product.product_type == product_type)
	return pd.read_sql(stmt, engine)
