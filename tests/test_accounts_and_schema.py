import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import portfolio_manager.storage.migrations as migrations
import portfolio_manager.storage.models as tables
import portfolio_manager.storage.queries as sql
from portfolio_manager.config.account_settings import DEFAULT_BENCHMARKS, Benchmark, BenchmarkComponent
from portfolio_manager.config.accounts import (
    Account,
    Brokers,
    add_account,
    credentials_file_name,
    default_account,
    get_account,
    list_accounts,
)


def test_new_database_starts_at_latest_version_without_accounts(temp_db):
    assert migrations.schema_version(temp_db) == migrations.latest_version()
    assert list_accounts() == [] and default_account() is None


def test_added_accounts_keep_their_order_and_get_the_default_benchmark_of_their_currency(temp_db):
    chf = add_account('Portfolio CHF', Brokers.DEGIRO, currency='CHF')
    usd = add_account(' Portfolio USD ', Brokers.DEGIRO, credentials_file='usd.json', currency='USD')
    eur = add_account('Portfolio EUR', Brokers.DEGIRO)  # currency not known yet
    assert chf == Account('Portfolio CHF', Brokers.DEGIRO, 'portfolio_chf.json', 'CHF')
    assert list_accounts() == [chf, usd, eur] and default_account() == chf
    assert get_account('Portfolio USD') == Account('Portfolio USD', Brokers.DEGIRO, 'usd.json', 'USD')
    assert get_account('Unknown') is None
    assert sql.query_benchmark('Portfolio CHF') == DEFAULT_BENCHMARKS['CHF']
    assert sql.query_benchmark('Portfolio USD') is None and sql.query_benchmark('Portfolio EUR') is None


@pytest.mark.parametrize(
    ('arguments', 'message'),
    [
        ({'name': ' '}, 'needs a name'),
        ({'name': 'Portfolio CHF'}, 'already exists'),
        ({'name': 'B', 'credentials_file': '../secret.json'}, 'credentials file'),
        ({'name': 'B', 'currency': 'chf'}, 'currency'),
    ],
)
def test_invalid_account_is_not_added(temp_db, arguments, message):
    add_account('Portfolio CHF', Brokers.DEGIRO, currency='CHF')
    with pytest.raises(ValueError, match=message):
        add_account(**{'broker': Brokers.DEGIRO, **arguments})
    assert [a.name for a in list_accounts()] == ['Portfolio CHF']


def test_credentials_file_name():
    assert credentials_file_name('Portfolio USD') == 'portfolio_usd.json'
    assert credentials_file_name(' My  Pensions (2) ') == 'my_pensions_2.json'


def test_saved_settings_are_never_replaced_by_the_defaults(temp_db):
    add_account('Portfolio CHF', Brokers.DEGIRO, currency='CHF')
    custom = Benchmark([BenchmarkComponent('SPY', 'SPY', 0.7), BenchmarkComponent('AGG', 'AGG', 0.3)], 'Q')
    sql.save_benchmark('Portfolio CHF', custom)
    tables.init_db()
    assert sql.query_benchmark('Portfolio CHF') == custom


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    # a database saved before the schema versions (some tables, no version)
    folder = tmp_path / 'db'
    folder.mkdir()
    engine = create_engine(f'sqlite:///{folder / "old.db"}')
    tables.Product.__table__.create(engine)
    monkeypatch.setattr(tables, 'engine', engine)
    monkeypatch.setattr(sql, 'engine', engine)
    monkeypatch.setattr(sql, 'SessionLocal', sessionmaker(bind=engine, autoflush=False))
    return engine


def test_database_of_version_1_gets_the_accounts_defined_in_the_code(old_db, tmp_path):
    tables.init_db()
    assert migrations.schema_version(old_db) == migrations.latest_version()
    assert os.path.isfile(tmp_path / 'db' / 'old.db.bak-v1')  # copied at the baseline, before step 2
    assert list_accounts() == [
        Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF'),
        Account('Portfolio EUR', Brokers.DEGIRO, 'config_2.json', 'EUR'),
    ]
    assert sql.query_benchmark('Portfolio EUR') == DEFAULT_BENCHMARKS['EUR']


def test_upgrade_steps_run_once_in_order_after_a_backup(temp_db, tmp_path, monkeypatch):
    calls = []
    current = migrations.latest_version()

    def step(version):
        def run(connection):
            calls.append(version)
            connection.execute(text(f'CREATE TABLE added_{version} (x INTEGER)'))

        return run

    monkeypatch.setattr(migrations, 'UPGRADE_STEPS', {current + 1: step(current + 1), current + 2: step(current + 2)})
    tables.init_db()
    tables.init_db()  # nothing left to run
    assert calls == [current + 1, current + 2] and migrations.schema_version(temp_db) == current + 2
    assert {f'added_{current + 1}', f'added_{current + 2}'} <= set(inspect(temp_db).get_table_names())
    assert os.path.isfile(tmp_path / 'db' / f'test.db.bak-v{current}')


def test_failed_step_keeps_the_previous_version(temp_db, monkeypatch):
    current = migrations.latest_version()

    def failing(connection):
        connection.execute(text('CREATE TABLE half_done (x INTEGER)'))
        raise ValueError('conversion failed')

    monkeypatch.setattr(migrations, 'UPGRADE_STEPS', {current + 1: failing})
    with pytest.raises(ValueError):
        tables.init_db()
    assert migrations.schema_version(temp_db) == current


def test_database_newer_than_the_code_is_not_touched(temp_db):
    with temp_db.begin() as connection:
        migrations._set_version(connection, migrations.latest_version() + 1)
    with pytest.raises(RuntimeError, match='newer'):
        tables.init_db()


@pytest.mark.parametrize(
    ('components', 'freq', 'message'),
    [
        ([], 'M', 'no instruments'),
        ([BenchmarkComponent('A', 'A', 0.5), BenchmarkComponent('A', 'B', 0.5)], 'M', 'different label'),
        ([BenchmarkComponent('A', ' ', 1.0)], 'M', 'ISIN or a ticker'),
        ([BenchmarkComponent('A', 'A', 1.2), BenchmarkComponent('B', 'B', -0.2)], 'M', 'positive'),
        ([BenchmarkComponent('A', 'A', 0.5), BenchmarkComponent('B', 'B', 0.4)], 'M', '100%'),
        ([BenchmarkComponent('A', 'A', 1.0)], 'W', 'frequency'),
    ],
)
def test_invalid_benchmark_is_not_saved(temp_db, components, freq, message):
    with pytest.raises(ValueError, match=message):
        sql.save_benchmark('X', Benchmark(components, freq))
    assert sql.query_benchmark('X') is None
