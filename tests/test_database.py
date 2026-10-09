import os

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import portfolio_manager.storage.models as tables
import portfolio_manager.storage.queries as sql
from portfolio_manager.storage.models import Product


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    # empty database file in a temporary folder, used instead of the project database
    engine = create_engine(f'sqlite:///{tmp_path / "db" / "test.db"}')
    monkeypatch.setattr(tables, 'engine', engine)
    monkeypatch.setattr(tables, 'DATA_DIR', str(tmp_path / 'db'))
    monkeypatch.setattr(sql, 'engine', engine)
    monkeypatch.setattr(sql, 'SessionLocal', sessionmaker(bind=engine, autoflush=False))
    tables.init_db()
    return engine


def test_init_db_creates_folder_tables_and_indexes(temp_db, tmp_path):
    assert os.path.isfile(tmp_path / 'db' / 'test.db')
    inspector = inspect(temp_db)
    assert {'products', 'degiro_hist', 'yahoo_finance_info', 'degiro_yahoo_map'} <= set(inspector.get_table_names())
    assert any(ix['unique'] for ix in inspector.get_indexes('degiro_hist'))
    tables.init_db()  # running it again changes nothing


def test_degiro_yahoo_map_upsert(temp_db):
    sql.upsert_degiro_yahoo_map(product_ids=[1, 2], ticker='AAA.L')
    sql.upsert_degiro_yahoo_map(product_ids=[2], ticker='BBB.L')
    assert sql.query_degiro_yahoo_map([1, 2, 3]) == {1: 'AAA.L', 2: 'BBB.L'}


def test_yahoo_finance_info_upsert(temp_db):
    sql.upsert_yahoo_finance_info('AAA.L', {'currency': 'USD', 'isin': 'XX0000000001', 'longName': None})
    sql.upsert_yahoo_finance_info('AAA.L', {'currency': 'GBP'})
    assert sql.query_yahoo_finance_info_field(['AAA.L'], 'currency') == {'AAA.L': 'GBP'}
    assert sql.query_yahoo_finance_info_field(['AAA.L'], 'longName') == {}


def test_adjusted_prices_replace_whole_history(temp_db):
    first = pd.Series([1.0, 2.0, 3.0], index=pd.bdate_range('2024-01-01', periods=3))
    sql.replace_portfolio_instr_adj_close('AAA.L', first)
    second = pd.Series([1.5, 2.5], index=pd.bdate_range('2024-01-02', periods=2))
    sql.replace_portfolio_instr_adj_close('AAA.L', second)
    stored = sql.query_portfolio_instr_adj_close(['AAA.L'])['AAA.L']
    assert stored.tolist() == [1.5, 2.5]


def test_degiro_hist_keeps_stored_values_for_missing_fields(temp_db):
    day = pd.Timestamp('2024-01-02')
    sql.insert_degiro_hist(1, pd.DataFrame({'close': [10.0], 'volume': [500.0]}, index=[day]))
    sql.insert_degiro_hist(1, pd.DataFrame({'close': [11.0]}, index=[day]))
    stored = pd.read_sql('SELECT close, volume FROM degiro_hist WHERE product_id = 1', temp_db)
    assert stored.iloc[0].tolist() == [11.0, 500.0]


def test_commit_skips_duplicates_and_session_stays_usable(temp_db):
    with sql.SessionLocal() as session:
        session.add(Product(id=1, name='first'))
        sql._commit(session)
        session.add(Product(id=1, name='duplicate'))
        sql._commit(session, message='duplicate product')  # UNIQUE constraint: rolled back and skipped
        session.add(Product(id=2, name='second'))
        sql._commit(session)
    names = pd.read_sql('SELECT id, name FROM products ORDER BY id', temp_db)
    assert names.values.tolist() == [[1, 'first'], [2, 'second']]


def transactions(ids, product_ids, quantities, dates) -> pd.DataFrame:
    # Degiro transactions as downloaded: indexed by date, with an id column
    return pd.DataFrame(
        {'id': ids, 'product_id': product_ids, 'quantity': quantities, 'price': 10.0, 'transfered': False},
        index=pd.DatetimeIndex(pd.to_datetime(dates), name='date'),
    )


def test_degiro_records_round_trip_and_keep_order(temp_db):
    # two records at the same time come back sorted by id
    df = transactions([12, 11, 13], [101, 101, 202], [5, -2, 3], ['2024-01-02 10:00'] * 2 + ['2024-01-01 09:30'])
    sql.upsert_degiro_records(tables.DegiroTransaction, 'A', df)
    sql.upsert_degiro_records(tables.DegiroTransaction, 'B', df.iloc[:1])
    stored = sql.query_degiro_records(tables.DegiroTransaction, 'A')
    assert stored['id'].tolist() == [13, 11, 12]
    assert (
        stored.index.tolist() == pd.to_datetime(['2024-01-01 09:30', '2024-01-02 10:00', '2024-01-02 10:00']).tolist()
    )
    assert stored['quantity'].tolist() == [3, -2, 5] and stored['transfered'].tolist() == [False] * 3
    assert sql.query_account_product_ids('A') == [101, 202] and sql.query_account_product_ids('B') == [101]


def test_degiro_records_update_or_keep_stored_values(temp_db):
    df = transactions([1], [101], [5], ['2024-01-02'])
    sql.upsert_degiro_records(tables.DegiroTransaction, 'A', df)
    sql.upsert_degiro_records(tables.DegiroTransaction, 'A', df.assign(quantity=7), overwrite=False)
    assert sql.query_degiro_records(tables.DegiroTransaction, 'A')['quantity'].tolist() == [5]
    sql.upsert_degiro_records(tables.DegiroTransaction, 'A', df.assign(quantity=7))
    assert sql.query_degiro_records(tables.DegiroTransaction, 'A')['quantity'].tolist() == [7]


def test_cash_movements_without_product(temp_db):
    df = pd.DataFrame(
        {'id': [1, 2], 'product_id': [float('nan'), 101.0], 'change': [100.0, 1.5], 'balance': [{'total': 1.0}, None]},
        index=pd.DatetimeIndex(pd.to_datetime(['2024-01-02', '2024-01-03']), name='date'),
    )
    sql.upsert_degiro_records(tables.DegiroCashMovement, 'A', df)
    stored = sql.query_degiro_records(tables.DegiroCashMovement, 'A')
    assert stored['product_id'].isna().tolist() == [True, False] and stored['product_id'].iloc[1] == 101
    assert stored['balance'].tolist() == ["{'total': 1.0}", None]


def test_products_upsert(temp_db):
    df = pd.DataFrame(
        {
            'id': [1, 2],
            'name': ['First', 'Second'],
            'vwd_id': ['485012177', 'IE00B3YLTY66.TRADE,E'],  # a number or a text key
            'buy_order_types': [['LIMIT', 'MARKET'], None],
            'close_price_date': ['2026-10-07', None],
            'tradable': [True, False],
        }
    )
    sql.upsert_products(df)
    sql.upsert_products(df.assign(name=['Renamed', 'Renamed']).iloc[:1], overwrite=False)
    stored = sql.query_products(product_id=[1, 2])
    assert stored['name'].tolist() == ['First', 'Second']
    assert stored['vwd_id'].tolist() == [485012177, 'IE00B3YLTY66.TRADE,E']
    assert stored.loc[1, 'buy_order_types'] == 'LIMIT, MARKET' and stored.loc[1, 'close_price_date'].year == 2026
    sql.upsert_products(df.assign(name=['Renamed', 'Renamed']).iloc[:1])
    assert sql.query_products(product_id=1)['name'].tolist() == ['Renamed']


def test_degiro_hist_without_overwrite_adds_missing_days_only(temp_db):
    days = pd.bdate_range('2024-01-01', periods=2)
    sql.insert_degiro_hist(1, pd.DataFrame({'price': [10.0]}, index=days[:1]))
    sql.insert_degiro_hist(1, pd.DataFrame({'price': [9.0, 11.0]}, index=days), overwrite=False)
    assert sql.query_degiro_hist(1, 'price')[1].tolist() == [10.0, 11.0]
