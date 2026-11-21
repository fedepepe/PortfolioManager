"""SQLite database engine and session factory.

The dashboard server reads and writes the database from several threads at once (the pages, the Update button, the
background update of the prices, the rerun of the research). Every connection therefore uses:
- write-ahead logging (WAL): readers and the writer do not block each other, only two writers wait for each other
  (in the default journal mode a long read, e.g. the prices of thousands of listings, blocks every write);
- a busy timeout of BUSY_TIMEOUT_SECONDS: a writer waits that long for another one instead of failing with "database is
  locked" after 5 seconds.
With WAL, SQLite keeps two files next to the database while it is open (degiro.db-wal and degiro.db-shm); they are
folded back into it when the last connection closes.
"""

import logging
import os
import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from portfolio_manager.config.settings import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(DATA_DIR, 'degiro.db')
BUSY_TIMEOUT_SECONDS = 30


@event.listens_for(Engine, 'connect')
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Busy timeout and write-ahead logging for every new SQLite connection (see the module)."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f'PRAGMA busy_timeout = {BUSY_TIMEOUT_SECONDS * 1000}')
        try:  # kept in the database file once set; needs a moment without other connections the first time
            cursor.execute('PRAGMA journal_mode = WAL')
        except sqlite3.OperationalError as e:
            logger.warning('Database left in its journal mode (write-ahead logging not set): %s', e)
    finally:
        cursor.close()


# creating the engine does not open the database: it is opened at the first query
engine = create_engine(f'sqlite:///{DB_PATH}')
# sessions are opened per operation (with SessionLocal() as session: ...), never shared
SessionLocal = sessionmaker(bind=engine, autoflush=False)

Base = declarative_base()
