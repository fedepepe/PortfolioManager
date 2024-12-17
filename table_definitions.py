from sqlalchemy import Column, Integer, String, Boolean, Float, Date, UniqueConstraint

from db_conn import Base, engine


class Product(Base):
	__tablename__ = 'products'
	active = Column(Boolean)
	buy_order_types = Column(String)
	category = Column(String)
	close_price = Column(Float)
	close_price_date = Column(Date)
	contract_size = Column(Integer)
	currency = Column(String)
	exchange_id = Column(Integer)
	feed_quality = Column(String)
	feed_quality_secondary = Column(String)
	id = Column(Integer, primary_key=True)
	is_shortable = Column(Boolean)
	isin = Column(String)
	name = Column(String)
	only_eod_prices = Column(Boolean)
	order_book_depth = Column(Float)
	order_book_depth_secondary = Column(Float)
	order_time_types = Column(String)
	product_bit_types = Column(String)
	product_type = Column(String)
	product_type_id = Column(Integer)
	quality_switch_free = Column(Boolean)
	quality_switch_free_secondary = Column(Boolean)
	quality_switchable = Column(Boolean)
	quality_switchable_secondary = Column(Boolean)
	sell_order_types = Column(String)
	strike_price = Column(Float)
	symbol = Column(String)
	tradable = Column(Boolean)
	vwd_id = Column(Integer)
	vwd_id_secondary = Column(Integer)
	vwd_identifier_type = Column(String)
	vwd_identifier_type_secondary = Column(String)
	vwd_module_id = Column(Integer)
	vwd_module_id_secondary = Column(Integer)
	__table_args__ = (UniqueConstraint('id', name='_id_unique'), )


# Create the table in the database
Base.metadata.create_all(engine)
