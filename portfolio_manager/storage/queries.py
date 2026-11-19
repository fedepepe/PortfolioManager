"""Reads and writes of the database; every write opens its own session."""

import json
import logging
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, delete, func, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_manager.config.account_settings import Benchmark
from portfolio_manager.config.account_settings import BenchmarkComponent as Component
from portfolio_manager.config.allocations import Allocation, AllocationInstrument, Bucket
from portfolio_manager.config.markets import MarketInstrument
from portfolio_manager.config.taa import RateSeries, TaaInstrument
from portfolio_manager.degiro.definitions import Exchanges, ProductTypes
from portfolio_manager.degiro.models import DegiroProductItem
from portfolio_manager.optimization.settings import OptimizationSettings
from portfolio_manager.storage.db import SessionLocal, engine
from portfolio_manager.storage.models import (
    AccountSetting,
    BenchmarkComponent,
    BrokerAccount,
    CurrencyPair,
    DegiroCashMovement,
    DegiroHistData,
    DegiroTransaction,
    DegiroYahooMap,
    EtfCatalogEntry,
    EtfClass,
    InterestRate,
    MarketInstrumentEntry,
    Product,
    SaaAllocation,
    SaaBucket,
    SaaClass,
    SaaInstrument,
    TaaInstrumentEntry,
    TaaRateSeriesEntry,
    YahooCurrencyFix,
    YahooInstrument,
    YahooPrice,
)
from portfolio_manager.storage.sql_utils import list_to_str, str_to_date

logger = logging.getLogger(__name__)

DEGIRO_HIST_COLS = ['open', 'high', 'low', 'close', 'price', 'volume']


def insert_product(product: DegiroProductItem):
    """Insert a Degiro product (skipped if already stored)."""
    data = Product(
        active=product.active,
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
    with SessionLocal() as session:
        session.add(data)
        _commit(session, message=f'{product.id} - {product.name}')


def insert_degiro_hist(product_id: int, df: pd.DataFrame, overwrite: bool = True):
    """Upsert historical data of one product; new non-null values overwrite stored ones, while a field missing from this
    fetch keeps its stored value. Without overwrite, only the days not stored yet are added.
    """
    df = df.reindex(columns=DEGIRO_HIST_COLS).dropna(how='all')
    if df.empty:
        return
    rows = [
        {
            'product_id': int(product_id),
            'date': pd.Timestamp(ts).date(),
            **{col: (None if pd.isna(val) else float(val)) for col, val in row.items()},
        }
        for ts, row in df.iterrows()
    ]
    stmt = sqlite_insert(DegiroHistData)
    keys = [DegiroHistData.product_id, DegiroHistData.date]
    if overwrite:
        stmt = stmt.on_conflict_do_update(
            index_elements=keys,
            set_={col: func.coalesce(stmt.excluded[col], DegiroHistData.__table__.c[col]) for col in DEGIRO_HIST_COLS},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)
    with SessionLocal() as session:
        session.execute(stmt, rows)
        _commit(session, message=f'Degiro historical data of product {product_id} ({len(rows)} rows)')


def _value(column_type: Any, value: Any) -> Any:
    # a frame value converted to the Python type of a table column (missing values become None)
    if isinstance(value, list | tuple):
        return list_to_str(value)
    if value is None or pd.isna(value):
        return None
    if isinstance(column_type, Boolean):
        return bool(value)
    if isinstance(column_type, Integer):
        # a Degiro vwd id is a number or a text key
        try:
            return int(value)
        except ValueError:
            return str(value)
    if isinstance(column_type, Float):
        return float(value)
    if isinstance(column_type, DateTime):
        return pd.Timestamp(value).to_pydatetime()
    if isinstance(column_type, Date):
        return pd.Timestamp(value).date()
    return str(value)


def _rows(model: Any, df: pd.DataFrame, **fixed: Any) -> list[dict[str, Any]]:
    # one row per frame row, with the frame columns that are table columns, plus fixed values
    table_columns = model.__table__.columns
    ignored = [c for c in df.columns if c not in table_columns]
    if ignored:
        logger.warning('Fields not stored in %s: %s', model.__tablename__, ignored)
    columns = [c for c in df.columns if c in table_columns]
    return [
        {**fixed, **{c: _value(table_columns[c].type, v) for c, v in zip(columns, values, strict=True)}}
        for values in df[columns].itertuples(index=False, name=None)
    ]


def upsert_products(df: pd.DataFrame, overwrite: bool = True):
    """Store products (one row per product, with the columns of the products table); with overwrite, the stored
    products are updated, otherwise only the new ones are added.
    """
    rows = _rows(Product, df)
    if not rows:
        return
    stmt = sqlite_insert(Product)
    if overwrite:
        stmt = stmt.on_conflict_do_update(
            index_elements=[Product.id], set_={c: stmt.excluded[c] for c in rows[0] if c != Product.id.name}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[Product.id])
    with SessionLocal() as session:
        session.execute(stmt, rows)
        _commit(session, message=f'{len(rows)} products')


DegiroRecord = DegiroTransaction | DegiroCashMovement


def upsert_degiro_records(model: type[DegiroRecord], account_name: str, df: pd.DataFrame, overwrite: bool = True):
    """Store the transactions or cash movements of an account (frame indexed by date, with an id column); with
    overwrite, stored records with the same id are updated, otherwise only the new ones are added.
    """
    rows = _rows(model, df.reset_index(), account=account_name)
    if not rows:
        return
    stmt = sqlite_insert(model)
    keys = [model.account, model.id]
    if overwrite:
        stmt = stmt.on_conflict_do_update(
            index_elements=keys, set_={c: stmt.excluded[c] for c in rows[0] if c not in ('account', 'id')}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=keys)
    with SessionLocal() as session:
        session.execute(stmt, rows)
        _commit(session, message=f'{len(rows)} {model.__tablename__} of {account_name}')


def query_degiro_records(model: type[DegiroRecord], account_name: str) -> pd.DataFrame:
    """Transactions or cash movements of an account, indexed by date (sorted), without the account column."""
    columns = [c for c in model.__table__.columns if c.name != 'account']
    stmt = select(*columns).where(model.account == account_name).order_by(model.date, model.id)
    df = pd.read_sql(stmt, engine)
    for column in columns:
        if isinstance(column.type, DateTime):
            df[column.name] = pd.to_datetime(df[column.name])
    return df.set_index('date')


def query_accounts() -> list[dict[str, Any]]:
    """The broker accounts in the order they were added: name, broker, currency, credentials_file."""
    columns = [BrokerAccount.name, BrokerAccount.broker, BrokerAccount.currency, BrokerAccount.credentials_file]
    with engine.connect() as connection:
        rows = connection.execute(select(*columns).order_by(BrokerAccount.position)).mappings().all()
    return [dict(row) for row in rows]


def insert_account(name: str, broker: str, credentials_file: str, currency: str | None = None):
    """Add a broker account after the existing ones; ValueError if an account has the same name."""
    with SessionLocal() as session:
        if session.get(BrokerAccount, name) is not None:
            raise ValueError(f'An account named {name} already exists')
        position = session.execute(select(func.coalesce(func.max(BrokerAccount.position) + 1, 0))).scalar()
        session.add(
            BrokerAccount(
                name=name, broker=broker, currency=currency, credentials_file=credentials_file, position=position
            )
        )
        session.commit()
    logger.info('Account %s added', name)


def update_account_currency(name: str, currency: str):
    """Save the base currency of an account."""
    with SessionLocal() as session:
        session.get(BrokerAccount, name).currency = currency
        session.commit()
    logger.info('Base currency of %s: %s', name, currency)


def upsert_currency_pairs(pairs: dict[str, int]):
    """Save currency pairs ({base}/{quote} -> product id of the exchange rate); a stored pair gets the new product."""
    rows = [
        {'pair': pair, 'base': pair[:3], 'quote': pair[4:], 'product_id': int(product_id)}
        for pair, product_id in pairs.items()
    ]
    if not rows:
        return
    stmt = sqlite_insert(CurrencyPair)
    stmt = stmt.on_conflict_do_update(index_elements=[CurrencyPair.pair], set_={'product_id': stmt.excluded.product_id})
    with SessionLocal() as session:
        session.execute(stmt, rows)
        _commit(session, message=f'{len(rows)} currency pairs')


def query_currency_pairs() -> dict[str, int]:
    """Currency pairs ({base}/{quote}) and the product id of their exchange rate."""
    with engine.connect() as connection:
        return dict(connection.execute(select(CurrencyPair.pair, CurrencyPair.product_id)).all())


def query_benchmark(account_name: str) -> Benchmark | None:
    """Saved benchmark of an account, None if it has no saved settings."""
    with SessionLocal() as session:
        setting = session.get(AccountSetting, account_name)
        if setting is None:
            return None
        rows = session.scalars(
            select(BenchmarkComponent)
            .where(BenchmarkComponent.account == account_name)
            .order_by(BenchmarkComponent.position)
        ).all()
        components = [Component(label=r.label, search=r.search, weight=r.weight, ticker=r.ticker) for r in rows]
        return Benchmark(components=components, rebalancing_freq=setting.benchmark_rebalancing_freq)


def save_benchmark(account_name: str, benchmark: Benchmark):
    """Save the benchmark of an account (validated first), replacing the saved one."""
    benchmark.validate()
    stmt = sqlite_insert(AccountSetting).values(
        account=account_name, benchmark_rebalancing_freq=benchmark.rebalancing_freq
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[AccountSetting.account],
        set_={'benchmark_rebalancing_freq': stmt.excluded.benchmark_rebalancing_freq},
    )
    with SessionLocal() as session:
        session.execute(stmt)
        session.execute(delete(BenchmarkComponent).where(BenchmarkComponent.account == account_name))
        session.add_all(
            BenchmarkComponent(
                account=account_name, label=c.label, search=c.search, weight=c.weight, ticker=c.ticker, position=n
            )
            for n, c in enumerate(benchmark.components)
        )
        session.commit()
    logger.info('Benchmark of %s saved: %s', account_name, benchmark.describe())


def save_benchmark_tickers(account_name: str, tickers: dict[str, str]):
    """Keep the Yahoo Finance listing found for instruments of the benchmark of an account (label -> ticker)."""
    with SessionLocal() as session:
        for label, ticker in tickers.items():
            component = session.get(BenchmarkComponent, (account_name, label))
            if component is not None:  # a benchmark that is not saved (a default one) keeps nothing
                component.ticker = ticker
        session.commit()


def query_account_product_ids(account_name: str) -> list[int]:
    """Products traded in an account (sorted ids)."""
    stmt = (
        select(DegiroTransaction.product_id)
        .where(DegiroTransaction.account == account_name)
        .distinct()
        .order_by(DegiroTransaction.product_id)
    )
    with engine.connect() as connection:
        return list(connection.execute(stmt).scalars())


YAHOO_INFO_COLUMNS = ['isin', 'name_short', 'name_long', 'currency', 'quote_type', 'exchange', 'market', 'region']
YAHOO_PRICE_COLUMNS = ['open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'capital_gain']


def upsert_yahoo_instrument(ticker: str, info: dict[str, Any] | None = None) -> int:
    """Save a Yahoo Finance listing with the given information fields (other stored fields are kept); returns its id."""
    values = {k: (None if v in (None, '') else str(v)) for k, v in (info or {}).items() if k in YAHOO_INFO_COLUMNS}
    stmt = sqlite_insert(YahooInstrument).values(ticker=ticker, **values)
    if values:
        stmt = stmt.on_conflict_do_update(
            index_elements=[YahooInstrument.ticker], set_={k: stmt.excluded[k] for k in values}
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[YahooInstrument.ticker])
    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()
        return session.execute(select(YahooInstrument.id).where(YahooInstrument.ticker == ticker)).scalar_one()


def query_yahoo_instruments(tickers: list[str] | None = None) -> pd.DataFrame:
    """Information of Yahoo Finance listings, indexed by ticker (every listing if no ticker is given)."""
    stmt = select(YahooInstrument)
    if tickers is not None:
        stmt = stmt.where(YahooInstrument.ticker.in_(tickers))
    return pd.read_sql(stmt, engine).set_index('ticker')


def query_listing_isins(tickers: list[str]) -> dict[str, str]:
    """Degiro ISIN of the fund of each Yahoo listing, from the products mapped to it (the most frequent if they differ,
    e.g. after a fund merger); listings no product with an ISIN is mapped to are left out.
    """
    stmt = select(DegiroYahooMap.ticker, DegiroYahooMap.isin).where(
        DegiroYahooMap.ticker.in_(tickers), DegiroYahooMap.isin.is_not(None)
    )
    df = pd.read_sql(stmt, engine)
    if df.empty:
        return {}
    return df.groupby('ticker')['isin'].agg(lambda s: s.mode().iloc[0]).to_dict()


def query_isin_listings(isins: list[str]) -> pd.DataFrame:
    """The Yahoo listings of funds (Degiro ISINs), from the products mapped to them: one row per listing (ticker,
    isin).
    """
    stmt = select(DegiroYahooMap.ticker, DegiroYahooMap.isin).where(
        DegiroYahooMap.isin.in_(isins), DegiroYahooMap.ticker.is_not(None)
    )
    return pd.read_sql(stmt.distinct(), engine).drop_duplicates('ticker').reset_index(drop=True)


def query_yahoo_last_date(instrument_id: int) -> date | None:
    """Last day of the stored history of a listing, None without history."""
    stmt = select(func.max(YahooPrice.date)).where(YahooPrice.instrument_id == instrument_id)
    with engine.connect() as connection:
        return connection.execute(stmt).scalar()


def save_yahoo_prices(instrument_id: int, df: pd.DataFrame, replace: bool = False):
    """Save the daily history of a listing (date index, YAHOO_PRICE_COLUMNS): the given days overwrite the stored ones;
    with replace, the stored history is deleted first.
    """
    df = df.reindex(columns=YAHOO_PRICE_COLUMNS)
    rows = [
        {
            'instrument_id': instrument_id,
            'date': pd.Timestamp(ts).date(),
            **{c: _value(Float(), v) for c, v in row.items()},
        }
        for ts, row in df.iterrows()
    ]
    stmt = sqlite_insert(YahooPrice)
    stmt = stmt.on_conflict_do_update(
        index_elements=[YahooPrice.instrument_id, YahooPrice.date],
        set_={c: stmt.excluded[c] for c in YAHOO_PRICE_COLUMNS},
    )
    with SessionLocal() as session:
        if replace:
            session.execute(delete(YahooPrice).where(YahooPrice.instrument_id == instrument_id))
        if rows:
            session.execute(stmt, rows)
        session.commit()


def query_yahoo_prices(
    tickers: list[str] | None = None, start: date | None = None, columns: list[str] | None = None
) -> pd.DataFrame:
    """Daily history of listings (every listing if no ticker is given), from a day on: one row per ticker and date,
    with the requested YAHOO_PRICE_COLUMNS (all by default), sorted by ticker and date.
    """
    columns = columns or YAHOO_PRICE_COLUMNS
    stmt = select(YahooInstrument.ticker, YahooPrice.date, *[YahooPrice.__table__.c[c] for c in columns]).join(
        YahooInstrument, YahooInstrument.id == YahooPrice.instrument_id
    )
    if tickers is not None:
        stmt = stmt.where(YahooInstrument.ticker.in_(tickers))
    if start is not None:
        stmt = stmt.where(YahooPrice.date >= start)
    df = pd.read_sql(stmt.order_by(YahooInstrument.ticker, YahooPrice.date), engine)
    df['date'] = pd.to_datetime(df['date'])
    return df


def query_traded_value_means(tickers: list[str], start: date, period: pd.DateOffset) -> pd.DataFrame:
    """Mean daily traded value (close x volume, in the quoted currency of each listing) over the period before the last
    day of each listing with a close and a volume, from a day on: columns last_date and traded_value by ticker
    (listings without such a day from start on are left out). Computed by the database: one row per listing is read.
    """
    tickers_by_id = dict(
        select_all(select(YahooInstrument.id, YahooInstrument.ticker).where(YahooInstrument.ticker.in_(tickers)))
    )
    traded = (YahooPrice.close.is_not(None)) & (YahooPrice.volume.is_not(None)) & (YahooPrice.date >= start)
    last_stmt = (
        select(YahooPrice.instrument_id, func.max(YahooPrice.date))
        .where(YahooPrice.instrument_id.in_(list(tickers_by_id)), traded)
        .group_by(YahooPrice.instrument_id)
    )
    last_dates = pd.Series(dict(select_all(last_stmt)), dtype=object)
    rows = []
    # one query per last day (listings mostly share a few): the period runs back from it
    for last_date, ids in last_dates.groupby(last_dates).groups.items():
        cutoff = (pd.Timestamp(last_date) - period).date()
        mean_stmt = (
            select(YahooPrice.instrument_id, func.avg(YahooPrice.close * YahooPrice.volume))
            .where(YahooPrice.instrument_id.in_([int(i) for i in ids]), traded, YahooPrice.date > cutoff)
            .group_by(YahooPrice.instrument_id)
        )
        rows += [(tickers_by_id[i], pd.Timestamp(last_date), value) for i, value in select_all(mean_stmt)]
    return pd.DataFrame(rows, columns=['ticker', 'last_date', 'traded_value']).set_index('ticker')


def query_first_close_dates(tickers: list[str]) -> pd.Series:
    """Day of the first stored close of each listing (listings without one are left out), by ticker."""
    stmt = (
        select(YahooInstrument.ticker, func.min(YahooPrice.date))
        .join(YahooInstrument, YahooInstrument.id == YahooPrice.instrument_id)
        .where(YahooInstrument.ticker.in_(tickers), YahooPrice.close.is_not(None))
        .group_by(YahooInstrument.ticker)
    )
    return pd.Series({ticker: pd.Timestamp(day) for ticker, day in select_all(stmt)}, dtype='datetime64[ns]')


def query_last_close_dates(tickers: list[str]) -> pd.Series:
    """Day of the last stored close of each listing (listings without one are left out), by ticker."""
    stmt = (
        select(YahooInstrument.ticker, func.max(YahooPrice.date))
        .join(YahooInstrument, YahooInstrument.id == YahooPrice.instrument_id)
        .where(YahooInstrument.ticker.in_(tickers), YahooPrice.close.is_not(None))
        .group_by(YahooInstrument.ticker)
    )
    return pd.Series({ticker: pd.Timestamp(day) for ticker, day in select_all(stmt)}, dtype='datetime64[ns]')


def _replace_list(model: Any, items: list[tuple]):
    """Replace the saved list of a table (market_instruments, taa_instruments, taa_rate_series) with the given items
    (named tuples with the columns of the table), in their order.
    """
    rows = [dict(item._asdict(), position=n) for n, item in enumerate(items)]
    with SessionLocal() as session:
        session.execute(delete(model))
        if rows:
            session.execute(insert(model), rows)
        session.commit()


def _query_list(model: Any, item: Any, active_only: bool = False) -> list:
    """The saved list of a table as named tuples, in its order (only the items in use if active_only)."""
    stmt = select(*[model.__table__.c[field] for field in item._fields]).order_by(model.position)
    if active_only:
        stmt = stmt.where(model.active.is_(True))
    return [item(*row) for row in select_all(stmt)]


def save_market_instruments(instruments: list[MarketInstrument]):
    """Save the instruments of the Markets page, in their order (replaces the saved ones)."""
    _replace_list(MarketInstrumentEntry, instruments)


def query_market_instruments() -> list[MarketInstrument]:
    """The instruments of the Markets page, in their order."""
    return _query_list(MarketInstrumentEntry, MarketInstrument)


def save_taa_instruments(instruments: list[TaaInstrument]):
    """Save the instruments of the Tactical Asset Allocation research, in their order (replaces the saved ones)."""
    _replace_list(TaaInstrumentEntry, instruments)


def query_taa_instruments(active_only: bool = True) -> list[TaaInstrument]:
    """The instruments of the Tactical Asset Allocation research, in their order (those in use by default)."""
    return _query_list(TaaInstrumentEntry, TaaInstrument, active_only)


def save_rate_series(series: list[RateSeries]):
    """Save the interest rate series of the research, in their order (replaces the saved ones; values are kept)."""
    _replace_list(TaaRateSeriesEntry, series)


def query_rate_series(active_only: bool = True) -> list[RateSeries]:
    """The interest rate series of the research, in their order (those in use by default)."""
    return _query_list(TaaRateSeriesEntry, RateSeries, active_only)


def save_interest_rates(series: str, values: pd.Series):
    """Save values of an interest rate series (by date, decimals): the given days overwrite the stored ones."""
    rows = [
        {'series': series, 'date': pd.Timestamp(day).date(), 'value': float(value)}
        for day, value in values.dropna().items()
    ]
    if not rows:
        return
    stmt = sqlite_insert(InterestRate)
    stmt = stmt.on_conflict_do_update(
        index_elements=[InterestRate.series, InterestRate.date], set_={'value': stmt.excluded.value}
    )
    with SessionLocal() as session:
        session.execute(stmt, rows)
        session.commit()


def query_interest_rates(series: list[str] | None = None, start: date | None = None) -> pd.DataFrame:
    """Stored interest rates (every series if none is given), from a day on: date x series, decimals."""
    stmt = select(InterestRate.series, InterestRate.date, InterestRate.value)
    if series is not None:
        stmt = stmt.where(InterestRate.series.in_(series))
    if start is not None:
        stmt = stmt.where(InterestRate.date >= start)
    df = pd.read_sql(stmt, engine)
    df['date'] = pd.to_datetime(df['date'])
    return df.pivot(index='date', columns='series', values='value').sort_index()


def query_interest_rate_last_dates() -> dict[str, date]:
    """Last stored day of each interest rate series, by key."""
    stmt = select(InterestRate.series, func.max(InterestRate.date)).group_by(InterestRate.series)
    return {series: day for series, day in select_all(stmt)}


def select_all(stmt) -> list[tuple]:
    """All the rows of a query, as tuples."""
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(stmt).all()]


def query_yahoo_tickers_with_history() -> list[str]:
    """Listings with a stored history (sorted tickers)."""
    stmt = select(YahooInstrument.ticker).where(
        select(YahooPrice.instrument_id).where(YahooPrice.instrument_id == YahooInstrument.id).exists()
    )
    with engine.connect() as connection:
        return sorted(connection.execute(stmt).scalars())


def search_yahoo_finance_instruments(text: str, limit: int = 20) -> pd.DataFrame:
    """Instruments whose Yahoo ticker or ISIN (the Degiro ISIN of the products mapped to the listing) starts with text
    or, from 3 characters, whose name contains it; one row per ticker (ticker, name, isin, currency): ticker matches
    first, then ISIN, then name matches.
    """
    columns = ['ticker', 'name', 'isin', 'currency']
    text = text.strip()
    if not text:
        return pd.DataFrame(columns=columns)
    pattern = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')  # typed text taken literally
    instr = YahooInstrument
    mapped = select(DegiroYahooMap.ticker).where(DegiroYahooMap.isin.ilike(f'{pattern}%', escape='\\'))
    conditions = [
        instr.ticker.ilike(f'{pattern}%', escape='\\'),
        instr.ticker.in_(mapped),
    ]
    if len(text) >= 3:
        conditions.append(
            instr.name_long.ilike(f'%{pattern}%', escape='\\') | instr.name_short.ilike(f'%{pattern}%', escape='\\')
        )
    tickers = []
    for cond in conditions:
        stmt = select(instr.ticker).where(cond).order_by(instr.ticker).limit(limit)
        tickers += [t for t in pd.read_sql(stmt, engine)['ticker'] if t not in tickers]
    tickers = tickers[:limit]
    df = query_yahoo_instruments(tickers).reindex(tickers)
    df['name'] = df['name_long'].where(df['name_long'].notna(), df['name_short'])
    df['isin'] = df.index.map(query_listing_isins(tickers))
    return df.rename_axis('ticker').reset_index()[columns]


def save_etf_catalog(account_name: str, df: pd.DataFrame):
    """Replace the ETF catalog of an account (one row per ticker, with the columns of the etf_catalog table)."""
    rows = _rows(EtfCatalogEntry, df, account=account_name)
    with SessionLocal() as session:
        session.execute(delete(EtfCatalogEntry).where(EtfCatalogEntry.account == account_name))
        if rows:
            session.execute(insert(EtfCatalogEntry), rows)
        session.commit()
    logger.info('ETF catalog of %s saved: %d ETFs', account_name, len(rows))


def query_etf_catalog(account_name: str) -> pd.DataFrame:
    """ETF catalog of an account, by rank (without the account column)."""
    columns = [c for c in EtfCatalogEntry.__table__.columns if c.name != 'account']
    stmt = select(*columns).where(EtfCatalogEntry.account == account_name).order_by(EtfCatalogEntry.rank)
    return pd.read_sql(stmt, engine)


def save_listing_search(product_ids: list[int], ticker: str | None, status: str, rule: str | None = None):
    """Save the result of the search of the Yahoo Finance listing of products (dated now), with the Degiro ISIN of each
    product (from the products table).
    """
    with SessionLocal() as session:
        ids = [int(p) for p in product_ids]
        isins = dict(session.execute(select(Product.id, Product.isin).where(Product.id.in_(ids))).all())
        rows = [
            {
                'product_id': p,
                'isin': isins.get(p),
                'ticker': ticker,
                'status': status,
                'rule': rule,
                'searched_at': datetime.now(),
            }
            for p in ids
        ]
        stmt = sqlite_insert(DegiroYahooMap)
        stmt = stmt.on_conflict_do_update(
            index_elements=[DegiroYahooMap.product_id],
            set_={c: stmt.excluded[c] for c in ['isin', 'ticker', 'status', 'rule', 'searched_at']},
        )
        session.execute(stmt, rows)
        session.commit()


def query_listing_searches(product_ids: list[int] | None = None) -> pd.DataFrame:
    """Searches of the Yahoo Finance listing of products (of every searched product if none is given): product_id,
    isin, ticker, status, rule, searched_at.
    """
    m = DegiroYahooMap
    stmt = select(m.product_id, m.isin, m.ticker, m.status, m.rule, m.searched_at)
    if product_ids is not None:
        stmt = stmt.where(DegiroYahooMap.product_id.in_([int(p) for p in product_ids]))
    df = pd.read_sql(stmt, engine)
    df['searched_at'] = pd.to_datetime(df['searched_at'])
    return df


def save_currency_fix(ticker: str, yahoo_currency: str | None, currency: str, gap: float, product_id: int):
    """Save (or replace) the currency of the prices of a Yahoo listing whose reported currency is wrong (dated now)."""
    row = {
        'ticker': ticker,
        'yahoo_currency': yahoo_currency,
        'currency': currency,
        'gap': float(gap),
        'product_id': int(product_id),
        'detected_at': datetime.now(),
    }
    stmt = sqlite_insert(YahooCurrencyFix).values(**row)
    stmt = stmt.on_conflict_do_update(index_elements=[YahooCurrencyFix.ticker], set_=row)
    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()


def delete_currency_fix(ticker: str):
    """Remove the currency fix of a Yahoo listing (none: nothing happens)."""
    with SessionLocal() as session:
        session.execute(delete(YahooCurrencyFix).where(YahooCurrencyFix.ticker == ticker))
        session.commit()


def save_etf_classes(df: pd.DataFrame):
    """Replace the asset classes of the ETFs (one row per ISIN, with the columns of the etf_classes table)."""
    rows = _rows(EtfClass, df)
    with SessionLocal() as session:
        session.execute(delete(EtfClass))
        if rows:
            session.execute(insert(EtfClass), rows)
        session.commit()


def query_etf_classes(isins: list[str] | None = None) -> pd.DataFrame:
    """Asset classes of the ETFs (all if no ISIN is given): by ISIN, the columns of etf_classes."""
    stmt = select(*EtfClass.__table__.columns)
    if isins is not None:
        stmt = stmt.where(EtfClass.isin.in_(isins))
    return pd.read_sql(stmt, engine).set_index('isin')


def delete_unused_currency_fixes() -> int:
    """Remove the currency fixes of the listings no product is mapped to any more; returns how many."""
    mapped = select(DegiroYahooMap.ticker).where(DegiroYahooMap.ticker.is_not(None))
    with SessionLocal() as session:
        result = session.execute(delete(YahooCurrencyFix).where(YahooCurrencyFix.ticker.not_in(mapped)))
        session.commit()
    return result.rowcount


def query_currency_fixes(tickers: list[str] | None = None) -> pd.DataFrame:
    """Currency fixes of Yahoo listings (all if no ticker is given): by ticker, the columns of yahoo_currency_fixes."""
    stmt = select(YahooCurrencyFix)
    if tickers is not None:
        stmt = stmt.where(YahooCurrencyFix.ticker.in_(tickers))
    columns = [c.name for c in YahooCurrencyFix.__table__.columns]
    with engine.connect() as connection:
        rows = connection.execute(stmt).all()
    return pd.DataFrame([tuple(r) for r in rows], columns=columns).set_index('ticker')


def query_degiro_yahoo_map(product_ids: list[int] | None = None) -> dict[int, str]:
    """Yahoo Finance listing of each product with one (of every such product if no product is given)."""
    stmt = select(DegiroYahooMap.product_id, DegiroYahooMap.ticker).where(DegiroYahooMap.ticker.is_not(None))
    if product_ids is not None:
        stmt = stmt.where(DegiroYahooMap.product_id.in_([int(p) for p in product_ids]))
    with engine.connect() as connection:
        return dict(connection.execute(stmt).all())


def _commit(session: Session, message: str | None = None):
    try:
        session.commit()
    except IntegrityError as e:
        # roll back the failed transaction, so that the session can still be used
        session.rollback()
        if 'UNIQUE constraint failed' in str(e.orig):
            if message is not None:
                logger.info('Entry %s already exists: skipped', message)
            return
        raise
    if message is not None:
        logger.debug('Added entry %s', message)


def query_products(
    product_name: str | None = None,
    product_id: int | list[int] | None = None,
    product_isin: str | None = None,
    product_symbol: str | None = None,
    product_type: ProductTypes | None = None,
    tradable: bool | None = None,
    exchange: Exchanges | int | None = None,
) -> pd.DataFrame:
    """Products of the Degiro catalog matching all the given filters."""
    query = select(Product)
    if isinstance(exchange, Exchanges):
        exchange = exchange.value
    if product_name is not None:
        query = query.where(Product.name == product_name)
    if isinstance(product_id, list):
        query = query.where(Product.id.in_([int(i) for i in product_id]))
    elif product_id is not None:
        query = query.where(Product.id == product_id)
    if product_isin is not None:
        query = query.where(Product.isin == product_isin)
    if product_symbol is not None:
        query = query.where(Product.symbol == product_symbol)
    if product_type is not None:
        query = query.where(Product.product_type == product_type)
    if tradable is not None:
        query = query.where(Product.tradable == tradable)
    if exchange is not None:
        query = query.where(Product.exchange_id == exchange)
    df = pd.read_sql(query, engine)
    df = df.set_index('id', drop=False)
    return df


def query_degiro_hist(
    product_ids: int | list[int],
    columns: str | list[str] | None = None,
    date_start: pd.Timestamp | None = None,
    date_stop: pd.Timestamp | None = None,
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """Returns a date x product_id dataframe per requested field (a single dataframe if one field is requested)."""
    if isinstance(product_ids, int):
        product_ids = [product_ids]
    if columns is None:
        columns = DEGIRO_HIST_COLS
    elif isinstance(columns, str):
        columns = [columns]
    cond = DegiroHistData.product_id.in_(product_ids)
    if date_start is not None:
        cond = cond & (DegiroHistData.date >= pd.Timestamp(date_start).date())
    if date_stop is not None:
        cond = cond & (DegiroHistData.date < pd.Timestamp(date_stop).date())
    stmt = select(
        DegiroHistData.product_id, DegiroHistData.date, *[DegiroHistData.__table__.c[col] for col in columns]
    ).where(cond)
    df = pd.read_sql(stmt, engine)
    df['date'] = pd.to_datetime(df['date'])
    data = {col: df.pivot(columns='product_id', index='date', values=col).sort_index() for col in columns}
    if len(columns) == 1:
        return data[columns[0]]
    return data


def get_max_product_id() -> int:
    """Largest product id in the catalog."""
    with SessionLocal() as session:
        return session.query(func.max(Product.id)).all()[0][0]


SAA_TABLES = (SaaInstrument, SaaBucket, SaaClass)  # the parts of an allocation, keyed by account and allocation


def save_allocation(account_name: str, allocation: Allocation):
    """Save a strategic asset allocation of an account (validated first), replacing the one with the same name."""
    allocation.validate()
    with SessionLocal() as session:
        _delete_allocation(session, account_name, allocation.name)
        key = {'account': account_name, 'allocation': allocation.name}
        settings = json.dumps({k: v for k, v in allocation.settings.to_series().items()})
        session.add(
            SaaAllocation(
                account=account_name,
                name=allocation.name,
                weight_mode=allocation.weight_mode,
                rebalancing_freq=allocation.rebalancing_freq,
                settings=settings,
                saved_at=datetime.now(),
            )
        )
        for n, (asset_class, weight) in enumerate(allocation.class_targets.items()):
            session.add(
                SaaClass(
                    **key, asset_class=asset_class, weight=weight, split=allocation.splits.get(asset_class), position=n
                )
            )
        for n, bucket in enumerate(allocation.buckets):
            session.add(
                SaaBucket(**key, asset_class=bucket.asset_class, value=bucket.value, weight=bucket.weight, position=n)
            )
        for n, instrument in enumerate(allocation.instruments):
            session.add(
                SaaInstrument(
                    **key,
                    ticker=instrument.ticker,
                    isin=instrument.isin,
                    asset_class=instrument.asset_class,
                    bucket=instrument.bucket,
                    weight=instrument.weight,
                    position=n,
                )
            )
        session.commit()


def query_allocation(account_name: str, name: str) -> Allocation | None:
    """A saved strategic asset allocation of an account, None if there is none with this name."""
    with SessionLocal() as session:
        saved = session.get(SaaAllocation, (account_name, name))
        if saved is None:
            return None

        def rows(table):
            stmt = select(table).where(table.account == account_name, table.allocation == name).order_by(table.position)
            return session.execute(stmt).scalars().all()

        classes, buckets, instruments = rows(SaaClass), rows(SaaBucket), rows(SaaInstrument)
        return Allocation(
            name=saved.name,
            class_targets={c.asset_class: c.weight for c in classes},
            splits={c.asset_class: c.split for c in classes if c.split is not None},
            buckets=[Bucket(b.asset_class, b.value, b.weight) for b in buckets],
            instruments=[
                AllocationInstrument(i.ticker, i.isin, i.asset_class, i.bucket, i.weight) for i in instruments
            ],
            weight_mode=saved.weight_mode,
            settings=OptimizationSettings.from_series(pd.Series(json.loads(saved.settings))),
            rebalancing_freq=saved.rebalancing_freq,
        )


def list_allocations(account_name: str) -> list[str]:
    """Names of the saved strategic asset allocations of an account, the last saved first."""
    stmt = select(SaaAllocation.name).where(SaaAllocation.account == account_name)
    with engine.connect() as connection:
        return list(connection.execute(stmt.order_by(SaaAllocation.saved_at.desc())).scalars())


def delete_allocation(account_name: str, name: str):
    """Delete a saved strategic asset allocation of an account (nothing happens if there is none)."""
    with SessionLocal() as session:
        _delete_allocation(session, account_name, name)
        session.commit()


def _delete_allocation(session: Session, account_name: str, name: str):
    for table in SAA_TABLES:
        session.execute(delete(table).where(table.account == account_name, table.allocation == name))
    session.execute(delete(SaaAllocation).where(SaaAllocation.account == account_name, SaaAllocation.name == name))
