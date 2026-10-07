from sqlalchemy import Column, Integer, String, Boolean, Float, Date, UniqueConstraint, Sequence, Index

from database.db_conn import Base, engine


# Degiro product catalog table
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


# Degiro historical market data table (one row per product and day)
# volume is stored as reported by Degiro's chart API (unit not verified)
class DegiroHistData(Base):
	__tablename__ = 'degiro_hist'
	id = Column(Integer, primary_key=True)
	product_id = Column(Integer, nullable=False)
	date = Column(Date, nullable=False)
	open = Column(Float)
	high = Column(Float)
	low = Column(Float)
	close = Column(Float)
	price = Column(Float)
	volume = Column(Float)
	__table_args__ = (Index('ix_degiro_hist_product_date', 'product_id', 'date', unique=True), )


# Yahoo Finance historical price table
class YahooFinanceHistData(Base):
	__tablename__ = 'yahoo_finance_hist'
	id = Column(Integer, Sequence('close_id_seq'), primary_key=True)
	ticker = Column(String)
	date = Column(Date)
	quote_type = Column(String)
	value = Column(Float)
	__table_args__ = (UniqueConstraint('id', name='_id_unique'),
	                  Index('ix_yf_hist_ticker_quote_date', 'ticker', 'quote_type', 'date', unique=True), )


# Yahoo Finance instruments catalog table
class YahooFinanceProdInfo(Base):
	__tablename__ = 'yahoo_finance_info'
	id = Column(Integer, Sequence('close_id_seq'), primary_key=True)
	ticker = Column(String)
	quote_type = Column(String)
	value = Column(String)
	__table_args__ = (UniqueConstraint('ticker', 'quote_type', name='_ticker_quote_unique'), )


# Yahoo Finance portfolio instruments historical price table
class YahooFinanceHistDataPfInstr(Base):
	__tablename__ = 'yahoo_finance_hist_pf_instr'
	id = Column(Integer, Sequence('close_id_seq'), primary_key=True)
	ticker = Column(String)
	date = Column(Date)
	quote_type = Column(String)
	value = Column(Float)
	__table_args__ = (UniqueConstraint('id', name='_id_unique'),
	                  Index('ix_yf_hist_pf_instr_ticker_quote_date', 'ticker', 'quote_type', 'date', unique=True), )


# Yahoo Finance listing chosen for each Degiro product (several products may share a listing)
class DegiroYahooMap(Base):
	__tablename__ = 'degiro_yahoo_map'
	product_id = Column(Integer, primary_key=True)
	ticker = Column(String, nullable=False)


# Create the table in the database
Base.metadata.create_all(engine)
# create_all does not add new indexes to existing tables
for table in Base.metadata.sorted_tables:
	for index in table.indexes:
		index.create(engine, checkfirst=True)
