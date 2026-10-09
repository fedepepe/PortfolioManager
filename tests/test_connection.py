import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import portfolio_manager.degiro.connection as connection
import portfolio_manager.storage.migrations as migrations
import portfolio_manager.storage.models as tables
import portfolio_manager.storage.queries as sql
from portfolio_manager.config.account_settings import DEFAULT_BENCHMARKS
from portfolio_manager.config.accounts import Account, Brokers, add_account, get_account
from portfolio_manager.degiro.charts import exchange_rate, load_fx_rates
from portfolio_manager.storage.models import Product

# account information as Degiro returns it (fields used by the code, plus pairs without a product)
ACCOUNT_INFO = {
    'data': {
        'clientId': 1,
        'baseCurrency': 'CHF',
        'currencyPairs': {
            'EURCHF': {'id': 714322, 'price': '0.93'},
            'USDCHF': {'id': '714321', 'price': '0.80'},
            'GBPCHF': {'id': 11839929, 'price': '1.07'},
            'EURBEF': {'id': -2, 'price': '-'},
            'AEDEUR': {'id': -1, 'price': '-'},
        },
    }
}


class FakeConnection:
    # a logged-in Degiro connection without network
    def __init__(self, info=ACCOUNT_INFO):
        self.info = info

    def get_account_info(self):
        return self.info


def test_account_info_keeps_the_pairs_with_a_product():
    currency, pairs = connection.account_info(FakeConnection())
    assert currency == 'CHF'
    assert pairs == {'EUR/CHF': 714322, 'USD/CHF': 714321, 'GBP/CHF': 11839929}


def test_no_account_information():
    with pytest.raises(ConnectionError):
        connection.account_info(FakeConnection(info=None))


def test_connect_saves_currency_pairs_and_default_benchmark(temp_db):
    account = add_account('Portfolio CHF', Brokers.DEGIRO)  # currency not known yet
    connected = connection.connect_account(account, conn=FakeConnection())
    assert connected.currency == 'CHF' and get_account('Portfolio CHF').currency == 'CHF'
    assert sql.query_currency_pairs() == {'EUR/CHF': 714322, 'USD/CHF': 714321, 'GBP/CHF': 11839929}
    assert sql.query_benchmark('Portfolio CHF') == DEFAULT_BENCHMARKS['CHF']
    connection.connect_account(connected, conn=FakeConnection())  # a second connection changes nothing
    assert get_account('Portfolio CHF') == connected


def test_connect_rejects_a_different_currency(temp_db):
    account = add_account('Portfolio EUR', Brokers.DEGIRO, currency='EUR')
    with pytest.raises(ValueError, match='Degiro reports CHF'):
        connection.connect_account(account, conn=FakeConnection())
    assert get_account('Portfolio EUR').currency == 'EUR' and sql.query_currency_pairs() == {}


def test_account_number_from_the_client_details_when_missing(monkeypatch):
    class Credentials:
        int_account = None

    class API:
        def __init__(self, credentials):
            self.credentials = credentials

        def connect(self):
            pass

        def get_client_details(self):
            return {'data': {'intAccount': 1234567}}

    monkeypatch.setattr(connection, 'build_credentials', lambda location: Credentials())
    monkeypatch.setattr(connection, 'API', API)
    conn = connection.get_degiro_connection(Account('A', Brokers.DEGIRO, 'a.json', 'CHF'))
    assert conn.credentials.int_account == 1234567


DAYS = pd.bdate_range('2026-01-05', periods=3)
PAIR_PRICES = pd.DataFrame(
    {'EUR/CHF': [0.93, 0.94, 0.95], 'USD/CHF': [0.80, 0.81, np.nan], 'EUR/GBP': [0.86, 0.87, 0.88]}, index=DAYS
)


@pytest.mark.parametrize(
    ('currency', 'base', 'expected'),
    [
        ('EUR', 'CHF', PAIR_PRICES['EUR/CHF']),  # direct pair
        ('CHF', 'EUR', 1.0 / PAIR_PRICES['EUR/CHF']),  # inverse pair
        ('GBP', 'CHF', PAIR_PRICES['EUR/CHF'] / PAIR_PRICES['EUR/GBP']),  # cross rate through EUR
    ],
)
def test_exchange_rate(currency, base, expected):
    rate = exchange_rate(PAIR_PRICES, currency, base)
    pd.testing.assert_series_equal(rate, expected.rename(f'{currency}/{base}'), check_exact=False, rtol=1e-12)


def test_no_exchange_rate():
    # GBP -> EUR exists, but no pair links EUR or USD to JPY
    assert exchange_rate(PAIR_PRICES, 'GBP', 'JPY') is None


def test_load_fx_rates_from_the_pairs_and_their_prices(temp_db):
    sql.upsert_currency_pairs({'EUR/CHF': 714322, 'USD/CHF': 714321})
    sql.insert_degiro_hist(714322, PAIR_PRICES[['EUR/CHF']].set_axis(['price'], axis=1))
    sql.insert_degiro_hist(714321, PAIR_PRICES[['USD/CHF']].set_axis(['price'], axis=1))
    account = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')
    rates = load_fx_rates(account, curr_foreign_lst=['EUR', 'USD', 'JPY'], index=DAYS)
    assert list(rates.columns) == ['EUR/CHF', 'USD/CHF', 'CHF/CHF']  # no JPY rate: left out with a warning
    assert rates['EUR/CHF'].tolist() == [0.93, 0.94, 0.95] and rates['CHF/CHF'].tolist() == [1.0] * 3
    assert rates['USD/CHF'].iloc[:2].tolist() == [0.80, 0.81] and np.isnan(rates['USD/CHF'].iloc[2])


def test_database_of_version_2_gets_the_currency_pairs_of_the_catalog(tmp_path, monkeypatch):
    folder = tmp_path / 'db'
    folder.mkdir()
    engine = create_engine(f'sqlite:///{folder / "old.db"}')
    tables.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        migrations._set_version(conn, 2)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            Product(id=714322, name='EUR/CHF', product_type='CURRENCY'),
            Product(id=5466016, name='USD-CAD X-RATE', product_type='CURRENCY'),
            Product(id=322154, name='CHFEUR', product_type='CURRENCY'),  # not a pair name
            Product(id=1531238, name='INR-EUR X-RATE', product_type='CURRENCY'),
            Product(id=1, name='EUR/USD', product_type='ETF'),
        ]
    )
    session.commit()
    monkeypatch.setattr(tables, 'engine', engine)
    monkeypatch.setattr(sql, 'engine', engine)
    monkeypatch.setattr(sql, 'SessionLocal', sessionmaker(bind=engine, autoflush=False))
    tables.init_db()
    assert migrations.schema_version(engine) == 3
    assert sql.query_currency_pairs() == {'EUR/CHF': 714322, 'USD/CAD': 5466016, 'INR/EUR': 1531238}
