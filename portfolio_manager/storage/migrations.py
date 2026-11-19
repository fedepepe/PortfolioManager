"""Version of the database schema and the steps that upgrade an older database.

create_all (in init_db) adds new tables and indexes, but cannot change existing tables or convert their data: such a
change gets an upgrade step, a function that brings the database from the previous version to its own, run once in one
transaction. Version 1 is the schema created by create_all when the versions were introduced: a database saved before
has no version and is at version 1 once create_all has run. A new database starts at the latest version. Before running
any step, the database file is copied next to it (degiro.db.bak-v{version}).
"""

import logging
import os
import re
import shutil
import time
from collections.abc import Callable

from sqlalchemy import Connection, Engine, inspect, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from portfolio_manager.config.accounts import attach_default_benchmark, list_accounts
from portfolio_manager.config.markets import DEFAULT_MARKET_INSTRUMENTS
from portfolio_manager.config.taa import DEFAULT_RATE_SERIES, DEFAULT_TAA_INSTRUMENTS
from portfolio_manager.storage.models import BrokerAccount, CurrencyPair, Product, SchemaVersion, YahooInstrument
from portfolio_manager.storage.queries import (
    query_market_instruments,
    query_rate_series,
    query_taa_instruments,
    save_market_instruments,
    save_rate_series,
    save_taa_instruments,
)

logger = logging.getLogger(__name__)

BASELINE_VERSION = 1


def _accounts_from_code(connection: Connection):
    # version 2: the accounts are saved in the database; up to version 1 they were defined in config/accounts.py
    rows = [
        {'name': 'Portfolio CHF', 'broker': 'Degiro', 'currency': 'CHF', 'credentials_file': 'config.json'},
        {'name': 'Portfolio EUR', 'broker': 'Degiro', 'currency': 'EUR', 'credentials_file': 'config_2.json'},
    ]
    stmt = sqlite_insert(BrokerAccount).on_conflict_do_nothing(index_elements=[BrokerAccount.name])
    connection.execute(stmt, [dict(row, position=n) for n, row in enumerate(rows)])


def _currency_pairs_from_catalog(connection: Connection):
    # version 3: the exchange rates are found through the currency pairs of the account information; up to version 2
    # through the currency products of the catalog, whose names give the pair (EUR/CHF, USD-CAD X-RATE)
    rows = connection.execute(select(Product.id, Product.name).where(Product.product_type == 'CURRENCY')).all()
    pairs = []
    for product_id, name in rows:
        match = re.fullmatch(r'([A-Z]{3})[/-]([A-Z]{3})( X-RATE)?', name or '')
        if match:
            base, quote = match.group(1), match.group(2)
            pairs.append({'pair': f'{base}/{quote}', 'base': base, 'quote': quote, 'product_id': product_id})
    if pairs:
        connection.execute(
            sqlite_insert(CurrencyPair).on_conflict_do_nothing(index_elements=[CurrencyPair.pair]), pairs
        )


# fields of the Yahoo Finance information up to version 3 (one row per ticker and field) and their columns since
YAHOO_INFO_FIELDS = {
    'shortName': 'name_short',
    'longName': 'name_long',
    'currency': 'currency',
    'quoteType': 'quote_type',
    'exchange': 'exchange',
    'market': 'market',
    'region': 'region',
}
OLD_YAHOO_TABLES = ['yahoo_finance_info', 'yahoo_finance_hist', 'yahoo_finance_hist_pf_instr']


def _yahoo_history_tables(connection: Connection):
    # version 4: Yahoo Finance listings in yahoo_instruments (one row per listing) and their daily history with the
    # corporate actions in yahoo_prices, downloaded again (the old tables had no dividends); the listing found for each
    # instrument of a benchmark is kept
    inspector = inspect(connection)
    if 'ticker' not in [c['name'] for c in inspector.get_columns('benchmark_components')]:
        connection.execute(text('ALTER TABLE benchmark_components ADD COLUMN ticker VARCHAR'))
    if 'yahoo_finance_info' in inspector.get_table_names():
        instruments: dict[str, dict] = {}
        for ticker, field, value in connection.execute(
            text('SELECT ticker, quote_type, value FROM yahoo_finance_info')
        ):
            if field in YAHOO_INFO_FIELDS:
                instruments.setdefault(ticker, {'ticker': ticker})[YAHOO_INFO_FIELDS[field]] = value or None
        rows = [{c: row.get(c) for c in ['ticker', *YAHOO_INFO_FIELDS.values()]} for row in instruments.values()]
        if rows:
            stmt = sqlite_insert(YahooInstrument).on_conflict_do_nothing(index_elements=[YahooInstrument.ticker])
            connection.execute(stmt, rows)
    for table in OLD_YAHOO_TABLES:
        connection.execute(text(f'DROP TABLE IF EXISTS {table}'))


def _listing_searches(connection: Connection):
    # version 5: the listing of a product may be missing (not found), with the status, rule and date of its search;
    # SQLite cannot drop the NOT NULL of a column: the table is rebuilt, the stored listings found by an earlier search
    columns = [c['name'] for c in inspect(connection).get_columns('degiro_yahoo_map')]
    if 'status' in columns:
        return
    connection.execute(
        text(
            'CREATE TABLE degiro_yahoo_map_v5 (product_id INTEGER NOT NULL PRIMARY KEY, ticker VARCHAR, '
            'status VARCHAR NOT NULL, rule VARCHAR, searched_at DATETIME)'
        )
    )
    connection.execute(
        text("INSERT INTO degiro_yahoo_map_v5 SELECT product_id, ticker, 'found', NULL, NULL FROM degiro_yahoo_map")
    )
    connection.execute(text('DROP TABLE degiro_yahoo_map'))
    connection.execute(text('ALTER TABLE degiro_yahoo_map_v5 RENAME TO degiro_yahoo_map'))


def _own_line_proposals(connection: Connection):
    # version 6: the own line of a product with another name is proposed to the user, who accepts or rejects it
    columns = [c['name'] for c in inspect(connection).get_columns('degiro_yahoo_map')]
    for column in ['own_line', 'own_line_name', 'own_line_decision']:
        if column not in columns:
            connection.execute(text(f'ALTER TABLE degiro_yahoo_map ADD COLUMN {column} VARCHAR'))


def _mapping_isin(connection: Connection):
    # version 7: the mapping keeps the Degiro ISIN of each product (taken from the products table for the stored
    # searches); the own line proposals are gone. The table is rebuilt: SQLite cannot drop columns before 3.35
    columns = [c['name'] for c in inspect(connection).get_columns('degiro_yahoo_map')]
    if 'isin' in columns:
        return
    connection.execute(
        text(
            'CREATE TABLE degiro_yahoo_map_v7 (product_id INTEGER NOT NULL PRIMARY KEY, isin VARCHAR, ticker VARCHAR, '
            'status VARCHAR NOT NULL, rule VARCHAR, searched_at DATETIME)'
        )
    )
    connection.execute(
        text(
            'INSERT INTO degiro_yahoo_map_v7 SELECT m.product_id, p.isin, m.ticker, m.status, m.rule, m.searched_at '
            'FROM degiro_yahoo_map m LEFT JOIN products p ON p.id = m.product_id'
        )
    )
    connection.execute(text('DROP TABLE degiro_yahoo_map'))
    connection.execute(text('ALTER TABLE degiro_yahoo_map_v7 RENAME TO degiro_yahoo_map'))


def _no_yahoo_isin(connection: Connection):
    # version 8: yahoo_instruments no longer has an ISIN: yfinance's comes from a name search on Business Insider (often
    # another security's) and older rows had the Degiro ISIN; a fund's ISIN is the Degiro one of degiro_yahoo_map
    columns = [c['name'] for c in inspect(connection).get_columns('yahoo_instruments')]
    if 'isin' in columns:
        connection.execute(text('ALTER TABLE yahoo_instruments DROP COLUMN isin'))


def _etf_sub_classes(connection: Connection):
    # version 9: the ETF classes have a sub-class, a region and the currency hedging (filled by classify-etfs)
    columns = [c['name'] for c in inspect(connection).get_columns('etf_classes')]
    for column, kind in [
        ('sub_class', 'VARCHAR'),
        ('region', 'VARCHAR'),
        ('region_source', 'VARCHAR'),
        ('hedged', 'BOOLEAN'),
        ('hedge_currency', 'VARCHAR'),
    ]:
        if column not in columns:
            connection.execute(text(f'ALTER TABLE etf_classes ADD COLUMN {column} {kind}'))


# version -> step upgrading the database from the previous version
UPGRADE_STEPS: dict[int, Callable[[Connection], None]] = {
    2: _accounts_from_code,
    3: _currency_pairs_from_catalog,
    4: _yahoo_history_tables,
    5: _listing_searches,
    6: _own_line_proposals,
    7: _mapping_isin,
    8: _no_yahoo_isin,
    9: _etf_sub_classes,
}
# steps that free much space: the database file is compacted after them
COMPACT_AFTER = {4}


def latest_version() -> int:
    """Version of the schema of this code."""
    return max(UPGRADE_STEPS, default=BASELINE_VERSION)


def schema_version(engine: Engine) -> int | None:
    """Version of the database schema, None if the database has none yet."""
    with engine.connect() as connection:
        return connection.execute(select(SchemaVersion.version)).scalar()


def _set_version(connection: Connection, version: int):
    stmt = sqlite_insert(SchemaVersion).values(id=1, version=version)
    connection.execute(stmt.on_conflict_do_update(index_elements=[SchemaVersion.id], set_={'version': version}))


def backup_database(engine: Engine, version: int) -> str:
    """Copy the database file next to it (degiro.db.bak-v{version}) and return the path of the copy."""
    path = engine.url.database
    backup_path = f'{path}.bak-v{version}'
    engine.dispose()  # no open connection while copying
    start = time.time()
    shutil.copyfile(path, backup_path)
    logger.info('Database copied to %s before upgrading it (%.0f s)', backup_path, time.time() - start)
    return backup_path


def upgrade_schema(engine: Engine, new_database: bool):
    """Run the upgrade steps the database misses, after copying it; record the version of a database without one."""
    latest = latest_version()
    version = schema_version(engine)
    if version is None:
        version = latest if new_database else BASELINE_VERSION
        with engine.begin() as connection:
            _set_version(connection, version)
    if version > latest:
        raise RuntimeError(
            f'The database has schema version {version}, newer than the version of this code ({latest}): '
            'run a newer version of the code'
        )
    pending = [v for v in sorted(UPGRADE_STEPS) if v > version]
    if not pending:
        return
    backup_database(engine, version)
    for step_version in pending:
        with engine.begin() as connection:
            UPGRADE_STEPS[step_version](connection)
            _set_version(connection, step_version)
        logger.info('Database upgraded to schema version %d', step_version)
    if COMPACT_AFTER.intersection(pending):
        compact_database(engine)


def compact_database(engine: Engine):
    """Rewrite the database file without the free space (VACUUM, outside of any transaction)."""
    path = engine.url.database
    size_before, start = os.path.getsize(path), time.time()
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text('VACUUM'))
    logger.info(
        'Database compacted from %.0f MB to %.0f MB (%.0f s)',
        size_before / 1e6,
        os.path.getsize(path) / 1e6,
        time.time() - start,
    )


def seed_default_settings():
    """Save the default benchmark of the currency of each account without a benchmark, and the starting lists of the
    instruments of the Markets page and of the Tactical Asset Allocation research and of its rate series when none is
    saved (saved settings are never changed).
    """
    if not query_market_instruments():
        save_market_instruments(DEFAULT_MARKET_INSTRUMENTS)
    if not query_taa_instruments(active_only=False):
        save_taa_instruments(DEFAULT_TAA_INSTRUMENTS)
    if not query_rate_series(active_only=False):
        save_rate_series(DEFAULT_RATE_SERIES)
    for account in list_accounts():
        attach_default_benchmark(account)
