"""Tables of the SQLite database."""

import os

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
    inspect,
)

from portfolio_manager.config.settings import DATA_DIR
from portfolio_manager.storage.db import Base, engine


# Degiro product catalog table
class Product(Base):
    """Degiro product catalog."""

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
    __table_args__ = (UniqueConstraint('id', name='_id_unique'),)


# Degiro historical market data table (one row per product and day)
# volume is stored as reported by Degiro's chart API (unit not verified)
class DegiroHistData(Base):
    """Degiro daily history (OHLC, price, volume) per product."""

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
    __table_args__ = (Index('ix_degiro_hist_product_date', 'product_id', 'date', unique=True),)


# Degiro transactions of each account (columns in the order Degiro returns them)
class DegiroTransaction(Base):
    """Degiro transactions (trades) of an account."""

    __tablename__ = 'degiro_transactions'
    account = Column(String, primary_key=True)
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    auto_fx_fee_in_base_currency = Column(Float)
    buysell = Column(String)
    counter_party = Column(String)
    executing_entity_id = Column(String)
    fee_in_base_currency = Column(Float)
    fx_rate = Column(Float)
    gross_fx_rate = Column(Float)
    nett_fx_rate = Column(Float)
    order_type_id = Column(Integer)
    price = Column(Float)
    product_id = Column(Integer, nullable=False)
    quantity = Column(Integer)
    total = Column(Float)
    total_fees_in_base_currency = Column(Float)
    total_in_base_currency = Column(Float)
    total_plus_all_fees_in_base_currency = Column(Float)
    total_plus_fee_in_base_currency = Column(Float)
    transfered = Column(Boolean)
    trading_venue = Column(String)
    transaction_type_id = Column(Integer)


# Degiro cash movements of each account: deposits, dividends, fees, currency conversions, ...
class DegiroCashMovement(Base):
    """Degiro cash movements of an account."""

    __tablename__ = 'degiro_cash_movements'
    account = Column(String, primary_key=True)
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    balance = Column(String)
    change = Column(Float)
    currency = Column(String)
    description = Column(String)
    product_id = Column(Integer)
    type = Column(String)
    value_date = Column(DateTime)


# Yahoo Finance historical price table
class YahooFinanceHistData(Base):
    """Yahoo Finance history of the ETF catalog, one row per ticker, date and field."""

    __tablename__ = 'yahoo_finance_hist'
    id = Column(Integer, Sequence('close_id_seq'), primary_key=True)
    ticker = Column(String)
    date = Column(Date)
    quote_type = Column(String)
    value = Column(Float)
    __table_args__ = (
        UniqueConstraint('id', name='_id_unique'),
        Index('ix_yf_hist_ticker_quote_date', 'ticker', 'quote_type', 'date', unique=True),
    )


# Yahoo Finance instruments catalog table
class YahooFinanceProdInfo(Base):
    """Yahoo Finance instrument information, one row per ticker and field."""

    __tablename__ = 'yahoo_finance_info'
    id = Column(Integer, Sequence('close_id_seq'), primary_key=True)
    ticker = Column(String)
    quote_type = Column(String)
    value = Column(String)
    __table_args__ = (UniqueConstraint('ticker', 'quote_type', name='_ticker_quote_unique'),)


# Yahoo Finance portfolio instruments historical price table
class YahooFinanceHistDataPfInstr(Base):
    """Yahoo Finance adjusted prices of the portfolio instruments."""

    __tablename__ = 'yahoo_finance_hist_pf_instr'
    id = Column(Integer, Sequence('close_id_seq'), primary_key=True)
    ticker = Column(String)
    date = Column(Date)
    quote_type = Column(String)
    value = Column(Float)
    __table_args__ = (
        UniqueConstraint('id', name='_id_unique'),
        Index('ix_yf_hist_pf_instr_ticker_quote_date', 'ticker', 'quote_type', 'date', unique=True),
    )


# Yahoo Finance listing chosen for each Degiro product (several products may share a listing)
class DegiroYahooMap(Base):
    """Yahoo Finance listing chosen for each Degiro product."""

    __tablename__ = 'degiro_yahoo_map'
    product_id = Column(Integer, primary_key=True)
    ticker = Column(String, nullable=False)


# Version of the database schema (one row), see storage/migrations.py
class SchemaVersion(Base):
    """Version of the database schema."""

    __tablename__ = 'schema_version'
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)


# Settings of each account chosen by the user
class AccountSetting(Base):
    """Settings of an account."""

    __tablename__ = 'account_settings'
    account = Column(String, primary_key=True)
    benchmark_rebalancing_freq = Column(String, nullable=False)


# Instruments of the benchmark of each account, with their weights
class BenchmarkComponent(Base):
    """One instrument of the benchmark of an account."""

    __tablename__ = 'benchmark_components'
    account = Column(String, primary_key=True)
    label = Column(String, primary_key=True)
    search = Column(String, nullable=False)  # ISIN or Yahoo Finance ticker searched for its prices
    weight = Column(Float, nullable=False)
    position = Column(Integer, nullable=False)  # display order


def init_db():
    """Create the database file, its missing tables and indexes, upgrade an older schema and add the default settings
    of new accounts (called once at start-up).
    """
    from portfolio_manager.storage.migrations import seed_default_settings, upgrade_schema

    os.makedirs(DATA_DIR, exist_ok=True)
    new_database = not inspect(engine).get_table_names()
    Base.metadata.create_all(engine)
    # create_all does not add new indexes to existing tables
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)
    upgrade_schema(engine, new_database=new_database)
    seed_default_settings()
