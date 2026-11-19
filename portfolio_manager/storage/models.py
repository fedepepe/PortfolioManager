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


# Yahoo Finance instruments (listings): information refreshed at their first download
class YahooInstrument(Base):
    """A Yahoo Finance listing and its information, as Yahoo reports it (the ISIN of a fund comes from Degiro: see
    degiro_yahoo_map).
    """

    __tablename__ = 'yahoo_instruments'
    id = Column(Integer, primary_key=True)
    ticker = Column(String, nullable=False, unique=True)
    name_short = Column(String)
    name_long = Column(String)
    currency = Column(String)  # as Yahoo reports it: GBp, ZAc and ILA are hundredths of GBP, ZAR and ILS
    quote_type = Column(String)
    exchange = Column(String)
    market = Column(String)
    region = Column(String)


# Yahoo Finance daily history, one row per instrument and day: prices adjusted for splits (not for dividends), and
# the corporate actions of the day (dividends and capital gains per share, split ratio); the adjusted close is computed
# from them
class YahooPrice(Base):
    """Yahoo Finance daily prices and corporate actions of an instrument."""

    __tablename__ = 'yahoo_prices'
    instrument_id = Column(Integer, primary_key=True)
    date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    dividend = Column(Float)
    split = Column(Float)
    capital_gain = Column(Float)
    __table_args__ = {'sqlite_with_rowid': False}  # the key is the table: no separate index


# Interest rates of the Tactical Asset Allocation research (config.taa.RATE_SERIES), one row per series and day (the
# last day of the month for monthly series), as decimals (0.025 for 2.5%)
class InterestRate(Base):
    """A value of an interest rate series."""

    __tablename__ = 'interest_rates'
    series = Column(String, primary_key=True)  # key of the series (config.taa.RateSeries.key)
    date = Column(Date, primary_key=True)
    value = Column(Float, nullable=False)
    __table_args__ = {'sqlite_with_rowid': False}  # the key is the table: no separate index


# ETF catalog of each account: the most traded ETFs and their performance metrics in the account currency
class EtfCatalogEntry(Base):
    """One ETF of the catalog of an account."""

    __tablename__ = 'etf_catalog'
    account = Column(String, primary_key=True)
    ticker = Column(String, primary_key=True)  # Yahoo Finance listing
    rank = Column(Integer, nullable=False)  # by average traded value, 1 = most traded
    traded_value = Column(Float)  # average daily traded value of the last 3 months, in the currency below
    currency = Column(String)  # currency of the listing
    total_return = Column(Float)
    pa_return = Column(Float)
    return_1y = Column(Float)
    return_3y = Column(Float)
    return_5y = Column(Float)
    volatility = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)
    best_month = Column(Float)
    worst_month = Column(Float)
    max_drawdown = Column(Float)
    skewness = Column(Float)
    built_at = Column(DateTime, nullable=False)


class EtfClass(Base):
    """Asset class of each ETF of the catalog (one row per ISIN: every line of a fund shares it), with the Degiro data
    it comes from: Degiro's asset allocation if usable, otherwise rules on the ETP type and the name (source).
    """

    __tablename__ = 'etf_classes'
    isin = Column(String, primary_key=True)
    name = Column(String)  # Degiro name of the fund
    asset_class = Column(String, nullable=False)  # Equity, Fixed Income, Money Market, Commodities, ...
    leveraged = Column(Boolean, nullable=False)  # leveraged or inverse (e.g. 3x, -1x, daily short)
    source = Column(String, nullable=False)  # degiro, etp type, name rule
    # sub-class within the class (equity segment, bond type, commodity or crypto group), region (of the equities, or
    # the market of the bonds held), where the region comes from (degiro, benchmark, name), currency hedging
    sub_class = Column(String)
    region = Column(String)
    region_source = Column(String)
    hedged = Column(Boolean)
    hedge_currency = Column(String)
    degiro_allocation = Column(String)  # Degiro's asset allocation (Shares, Bonds, ...), none if unknown
    degiro_region = Column(String)
    degiro_benchmark = Column(String)
    degiro_etp_type = Column(Integer)  # 1 ETF, 2 note (currency, leveraged), 3 ETC (commodity), 4 crypto ETP
    classified_at = Column(DateTime, nullable=False)


class YahooCurrencyFix(Base):
    """A Yahoo Finance listing whose prices are in another currency than the one Yahoo reports (e.g. USD prices labelled
    CHF on SIX), found by the mapping: its prices match those of the Degiro product only in that currency. Our
    correction, kept apart from the Yahoo data of yahoo_instruments; checked again at each mapping of the product.
    """

    __tablename__ = 'yahoo_currency_fixes'
    ticker = Column(String, primary_key=True)  # Yahoo Finance listing
    yahoo_currency = Column(String)  # as Yahoo reports it (none if Yahoo reports none)
    currency = Column(String, nullable=False)  # of the prices
    gap = Column(Float)  # median daily gap to the Degiro prices in that currency
    product_id = Column(Integer)  # Degiro product whose prices showed it
    detected_at = Column(DateTime, nullable=False)


# Yahoo Finance listing of each Degiro product (several products may share a listing)
class DegiroYahooMap(Base):
    """Search of the Yahoo Finance listing of each Degiro product: the ISIN of the product (from Degiro) when it was
    searched, the listing (none if not found), the status of the last search (found, not found, failed), the rule that
    chose the listing and when the search was made.
    """

    __tablename__ = 'degiro_yahoo_map'
    product_id = Column(Integer, primary_key=True)
    isin = Column(String)
    ticker = Column(String)
    status = Column(String, nullable=False)
    rule = Column(String)
    searched_at = Column(DateTime)


# Version of the database schema (one row), see storage/migrations.py
class SchemaVersion(Base):
    """Version of the database schema."""

    __tablename__ = 'schema_version'
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)


# Broker accounts added by the user
class BrokerAccount(Base):
    """A broker account: name (the portfolio name), broker, base currency (detected at the first connection) and
    credentials file (in the credentials folder).
    """

    __tablename__ = 'accounts'
    name = Column(String, primary_key=True)
    broker = Column(String, nullable=False)
    currency = Column(String)
    credentials_file = Column(String, nullable=False)
    position = Column(Integer, nullable=False)  # order in which the accounts were added


# Currency pairs traded at Degiro, with the product of their exchange rate (from the account information)
class CurrencyPair(Base):
    """A currency pair, e.g. EUR/CHF: the price of one EUR in CHF is the price of the product."""

    __tablename__ = 'currency_pairs'
    pair = Column(String, primary_key=True)  # {base}/{quote}
    base = Column(String, nullable=False)
    quote = Column(String, nullable=False)
    product_id = Column(Integer, nullable=False)


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
    ticker = Column(String)  # Yahoo Finance listing found by the search (searched again if missing)
    weight = Column(Float, nullable=False)
    position = Column(Integer, nullable=False)  # display order


# Strategic asset allocations of each account (see config.allocations), saved under a name
class SaaAllocation(Base):
    """A strategic asset allocation of an account: weight mode, rebalancing and optimization settings (JSON)."""

    __tablename__ = 'saa_allocations'
    account = Column(String, primary_key=True)
    name = Column(String, primary_key=True)
    weight_mode = Column(String, nullable=False)  # manual or optimize
    rebalancing_freq = Column(String, nullable=False)
    settings = Column(String, nullable=False)  # OptimizationSettings as JSON
    saved_at = Column(DateTime, nullable=False)


class SaaClass(Base):
    """The target weight of an asset class in an allocation, and how the class is split (none, region, sub_class)."""

    __tablename__ = 'saa_classes'
    account = Column(String, primary_key=True)
    allocation = Column(String, primary_key=True)
    asset_class = Column(String, primary_key=True)
    weight = Column(Float, nullable=False)  # fraction of the portfolio
    split = Column(String)
    position = Column(Integer, nullable=False)  # display order


class SaaBucket(Base):
    """A part of a split asset class in an allocation (a region or a sub-class) and its weight in the class."""

    __tablename__ = 'saa_buckets'
    account = Column(String, primary_key=True)
    allocation = Column(String, primary_key=True)
    asset_class = Column(String, primary_key=True)
    value = Column(String, primary_key=True)
    weight = Column(Float, nullable=False)  # fraction of the class
    position = Column(Integer, nullable=False)


class SaaInstrument(Base):
    """An instrument chosen for a bucket of an allocation, with its weight in the bucket."""

    __tablename__ = 'saa_instruments'
    account = Column(String, primary_key=True)
    allocation = Column(String, primary_key=True)
    ticker = Column(String, primary_key=True)  # Yahoo Finance listing
    isin = Column(String)  # Degiro ISIN of the fund
    asset_class = Column(String, nullable=False)
    bucket = Column(String)  # value of the bucket (None: the class is not split)
    weight = Column(Float, nullable=False)  # fraction of the bucket
    position = Column(Integer, nullable=False)


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
