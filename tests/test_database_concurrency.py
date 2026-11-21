import time

import portfolio_manager.storage.queries as sql
from portfolio_manager.storage.db import BUSY_TIMEOUT_SECONDS


def test_connections_use_write_ahead_logging_and_wait_for_a_busy_database(temp_db):
    with temp_db.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'
        assert connection.exec_driver_sql('PRAGMA busy_timeout').scalar() == BUSY_TIMEOUT_SECONDS * 1000


def test_a_long_read_does_not_block_a_write(temp_db):
    sql.upsert_yahoo_instrument('AAA', {'currency': 'CHF'})
    with temp_db.connect() as reader:
        # a read transaction left open, as a long query of the prices from another thread
        reader.exec_driver_sql('BEGIN')
        assert reader.exec_driver_sql('SELECT count(*) FROM yahoo_instruments').scalar() == 1
        start = time.time()
        sql.upsert_yahoo_instrument('BBB', {'currency': 'EUR'})  # e.g. the Update button saving data meanwhile
        assert time.time() - start < 2  # not waiting for the reader (in the default journal mode: "database is locked")
        # the reader keeps its snapshot until its transaction ends
        assert reader.exec_driver_sql('SELECT count(*) FROM yahoo_instruments').scalar() == 1
        reader.exec_driver_sql('COMMIT')
    assert sorted(sql.query_yahoo_instruments().index) == ['AAA', 'BBB']
