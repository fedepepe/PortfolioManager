import pytest
from sqlalchemy import create_engine, insert

import database.sql as sql
from database.db_conn import Base
from database.table_definitions import YahooFinanceProdInfo

INSTRUMENTS = {
    'HYLD.L': {'longName': 'iShares Global High Yield Corp Bond UCITS ETF USD (Dist)', 'isin': 'IE00B74DQ490',
               'currency': 'USD'},
    'HYLA.L': {'longName': 'iShares Global High Yield Corp Bond UCITS ETF', 'isin': 'IE00BYWZ0440',
               'currency': 'USD'},
    'IWDC.SW': {'longName': 'iShares MSCI World CHF Hedged UCITS ETF (Acc)', 'isin': 'IE00B8BVCK12',
                'currency': 'CHF'},
    'XYZ': {'shortName': 'Short name only 50% fund', 'isin': 'US0000000001', 'currency': 'USD'},
}


@pytest.fixture
def instruments_db(monkeypatch):
    # in-memory database with a few synthetic instruments, used instead of the project database
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine, tables=[YahooFinanceProdInfo.__table__])
    rows = [{'ticker': t, 'quote_type': k, 'value': v} for t, info in INSTRUMENTS.items() for k, v in info.items()]
    with engine.begin() as connection:
        connection.execute(insert(YahooFinanceProdInfo), rows)
    monkeypatch.setattr(sql, 'engine', engine)


def search(text):
    return sql.search_yahoo_finance_instruments(text)


def test_ticker_prefix(instruments_db):
    assert search('hyl')['ticker'].tolist() == ['HYLA.L', 'HYLD.L']


def test_isin_prefix(instruments_db):
    assert search('ie00b8bv')['ticker'].tolist() == ['IWDC.SW']


def test_name_contains_from_three_characters(instruments_db):
    assert search('msci world')['ticker'].tolist() == ['IWDC.SW']
    assert search('ms').empty


def test_short_name_used_without_long_name(instruments_db):
    assert search('XYZ')['name'].tolist() == ['Short name only 50% fund']


def test_wildcards_are_taken_literally(instruments_db):
    assert search('50%')['ticker'].tolist() == ['XYZ']
    assert search('_').empty


def test_ticker_matches_first_and_limit(instruments_db):
    result = sql.search_yahoo_finance_instruments('i', limit=2)  # tickers starting with "I", then ISINs
    assert result['ticker'].tolist() == ['IWDC.SW', 'HYLA.L']


def test_blank_text(instruments_db):
    assert search('  ').empty
