from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import portfolio_manager.backtest.adjusted_prices as adjusted_prices
import portfolio_manager.market_data.history as history
import portfolio_manager.storage.migrations as migrations
import portfolio_manager.storage.models as tables
import portfolio_manager.storage.queries as sql
from portfolio_manager.analytics.instruments import prices_to_base_curr
from portfolio_manager.config.account_settings import Benchmark, BenchmarkComponent
from portfolio_manager.config.accounts import Account, Brokers, add_account

DAYS = pd.bdate_range('2026-01-05', periods=8)


def yahoo_frame(closes, dividends=None, days=DAYS) -> pd.DataFrame:
    # a history as yfinance returns it: dates at midnight of the exchange, zeros for days without actions
    index = pd.DatetimeIndex(days[: len(closes)]).tz_localize('Europe/London')
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            'Open': closes,
            'High': closes + 1,
            'Low': closes - 1,
            'Close': closes,
            'Adj Close': closes,
            'Volume': 1000.0,
            'Dividends': dividends if dividends is not None else 0.0,
            'Stock Splits': 0.0,
            'Capital Gains': 0.0,
        },
        index=index,
    )


@pytest.fixture
def yahoo(temp_db, monkeypatch):
    # a fake Yahoo Finance: histories, search results and information by ticker
    source = SimpleNamespace(histories={}, quotes={}, info={}, downloads=[])

    class Ticker:
        isin = '-'

        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, interval, auto_adjust, actions, start=None, period=None):
            source.downloads.append((self.ticker, start))
            df = source.histories.get(self.ticker, yahoo_frame([]))
            return df if start is None else df[df.index.tz_localize(None) >= pd.Timestamp(start)]

    monkeypatch.setattr(history.yf, 'Ticker', Ticker)
    monkeypatch.setattr(history, 'search_ticker', lambda text: SimpleNamespace(quotes=source.quotes.get(text, [])))
    monkeypatch.setattr(history, 'get_ticker_info', lambda ticker: source.info.get(ticker, {}))
    return source


def test_download_keeps_exchange_dates_and_only_real_actions(yahoo):
    yahoo.histories['AAA.L'] = yahoo_frame([10.0, 11.0], dividends=[0.0, 0.5])
    df = history.download_history('AAA.L')
    assert list(df.columns) == sql.YAHOO_PRICE_COLUMNS
    assert df.index.tolist() == DAYS[:2].tolist()  # no time zone shift
    assert np.isnan(df['dividend'].iloc[0]) and df['dividend'].iloc[1] == 0.5 and df['split'].isna().all()


def test_adjusted_close_scales_the_closes_before_each_distribution():
    df = pd.DataFrame(
        {
            'close': [10.0, 11.0, 10.5, 12.0, np.nan, 12.5],
            'dividend': [np.nan, np.nan, 0.5, np.nan, 0.2, np.nan],  # the second on a day without close
            'capital_gain': [np.nan, np.nan, 0.1, np.nan, np.nan, np.nan],
        },
        index=DAYS[:6],
    )
    first, second = 1 - 0.6 / 11.0, 1 - 0.2 / 12.0
    expected = [10.0 * first * second, 11.0 * first * second, 10.5 * second, 12.0 * second, 12.5]
    adjusted = history.adjusted_close(df)
    assert adjusted.index.tolist() == DAYS[[0, 1, 2, 3, 5]].tolist()
    assert adjusted.tolist() == pytest.approx(expected, rel=1e-12)


def test_new_days_are_added_and_the_last_stored_day_rewritten(yahoo):
    yahoo.histories['AAA.L'] = yahoo_frame([10.0, 11.0, 12.0])
    assert history.refresh_history('AAA.L') == 'new'
    # the last stored day had been downloaded during the day: its final close is not a change
    yahoo.histories['AAA.L'] = yahoo_frame([10.0, 11.0, 12.5, 13.0, 14.0])
    assert history.refresh_history('AAA.L') == 'updated'
    assert history.stored_history('AAA.L')['close'].tolist() == [10.0, 11.0, 12.5, 13.0, 14.0]
    assert yahoo.downloads[-1] == ('AAA.L', (DAYS[2] - pd.Timedelta(days=history.OVERLAP_DAYS)).date())


def test_a_day_published_late_is_added(yahoo):
    full = yahoo_frame([10.0, 11.0, 12.0, 13.0])
    yahoo.histories['AAA.L'] = full.drop(full.index[2])  # the third day is missing at first
    history.refresh_history('AAA.L')
    yahoo.histories['AAA.L'] = full
    assert history.refresh_history('AAA.L') == 'updated'
    assert history.stored_history('AAA.L')['close'].tolist() == [10.0, 11.0, 12.0, 13.0]


def test_changed_history_is_downloaded_again(yahoo):
    yahoo.histories['AAA.L'] = yahoo_frame([10.0, 11.0, 12.0, 13.0])
    history.refresh_history('AAA.L')
    # a split: Yahoo divides every past close
    yahoo.histories['AAA.L'] = yahoo_frame([5.0, 5.5, 6.0, 6.5, 7.0])
    assert history.refresh_history('AAA.L') == 'replaced'
    assert history.stored_history('AAA.L')['close'].tolist() == [5.0, 5.5, 6.0, 6.5, 7.0]
    assert yahoo.downloads[-1] == ('AAA.L', None)  # whole history


def test_stored_history_kept_when_yahoo_returns_nothing(yahoo):
    yahoo.histories['AAA.L'] = yahoo_frame([10.0, 11.0])
    history.refresh_history('AAA.L')
    yahoo.histories['AAA.L'] = yahoo_frame([])
    assert history.refresh_history('AAA.L', full=True) == 'not found'
    assert history.stored_history('AAA.L')['close'].tolist() == [10.0, 11.0]


def test_search_keeps_the_listing_with_prices_and_the_closest_name(yahoo):
    isin = 'IE00B74DQ490'
    yahoo.quotes[isin] = [
        {'symbol': 'HYLD.L', 'longname': 'iShares Global High Yield Corp Bond UCITS ETF USD (Dist)'},
        {'symbol': 'IHYG.MI', 'longname': 'iShares Euro High Yield Corp Bond UCITS ETF'},
        {'symbol': 'HYLD.XC', 'longname': 'iShares Global High Yield Corp Bond UCITS ETF USD (Dist)'},  # no prices
    ]
    yahoo.histories['HYLD.L'] = yahoo_frame([85.0, 86.0])
    yahoo.histories['IHYG.MI'] = yahoo_frame([100.0, 101.0])
    yahoo.info['HYLD.L'] = {'currency': 'USD', 'longName': 'iShares Global High Yield', 'quoteType': 'ETF'}
    ticker = history.search_listing(search=isin, ticker='HYLD', name='iShares Global High Yield Corp Bond USD Dist')
    assert ticker == 'HYLD.L'
    stored = sql.query_yahoo_instruments(['HYLD.L']).loc['HYLD.L']
    assert (stored['isin'], stored['currency'], stored['quote_type']) == (isin, 'USD', 'ETF')
    assert history.stored_history('HYLD.L')['close'].tolist() == [85.0, 86.0]
    assert sql.query_yahoo_instruments(['IHYG.MI']).empty  # the other listings are not stored


def test_search_by_ticker_and_preferred_exchange(yahoo):
    # a search text that is not an ISIN finds nothing: the ticker is searched, on any known exchange
    yahoo.quotes['STHC'] = [{'symbol': 'STHC.SW'}, {'symbol': 'STHC.DE'}, {'symbol': 'STHCX'}]
    yahoo.histories['STHC.SW'] = yahoo_frame([120.0])
    yahoo.histories['STHC.DE'] = yahoo_frame([110.0])
    yahoo.histories['STHCX'] = yahoo_frame([1.0])
    assert history.search_listing(search='STHC.SW', ticker='STHC') == 'STHC.DE'  # Frankfurt first
    assert sql.query_yahoo_instruments(['STHC.DE']).loc['STHC.DE', 'isin'] is None
    assert history.search_listing(search='XX', ticker='NONE') is None


def test_minor_currency_units():
    assert history.major_currency('GBp') == ('GBP', 0.01)
    assert history.major_currency('ZAc') == ('ZAR', 0.01)
    assert history.major_currency('ILA') == ('ILS', 0.01)
    assert history.major_currency('usd') == ('USD', 1.0)


def test_prices_in_pence_are_converted_to_pounds(temp_db):
    sql.upsert_currency_pairs({'GBP/CHF': 11839929})
    sql.insert_degiro_hist(11839929, pd.DataFrame({'price': [1.10, 1.12]}, index=DAYS[:2]))
    account = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')
    prices = pd.DataFrame({'AAA.L': [100.0, 200.0], 'BBB.L': [1.0, 2.0]}, index=DAYS[:2])
    converted = prices_to_base_curr(account, prices, ['GBp', 'GBP'])
    assert converted['AAA.L'].tolist() == pytest.approx([1.10, 2.24])  # pence
    assert converted['BBB.L'].tolist() == pytest.approx([1.10, 2.24])  # pounds


def test_benchmark_listings_are_searched_once(yahoo, monkeypatch):
    monkeypatch.setattr(adjusted_prices, 'is_yahoo_reachable', lambda: True)
    account = add_account('Portfolio CHF', Brokers.DEGIRO, currency='CHF')
    benchmark = Benchmark([BenchmarkComponent('IWDC', 'IE00B8BVCK12', 0.6), BenchmarkComponent('STHC', 'STHC.SW', 0.4)])
    sql.save_benchmark(account.name, benchmark)
    yahoo.quotes['IE00B8BVCK12'] = [{'symbol': 'IWDC.SW'}]
    yahoo.quotes['STHC.SW'] = [{'symbol': 'STHC.SW'}]
    for ticker, closes in [('IWDC.SW', [94.0, 95.0]), ('STHC.SW', [120.0, 121.0])]:
        yahoo.histories[ticker] = yahoo_frame(closes)
        yahoo.info[ticker] = {'currency': 'CHF'}
    prices = adjusted_prices.get_benchmark_adj_prices(account, benchmark)
    assert list(prices.columns) == ['IWDC', 'STHC'] and prices['STHC'].tolist() == [120.0, 121.0]
    saved = sql.query_benchmark(account.name)
    assert [c.ticker for c in saved.components] == ['IWDC.SW', 'STHC.SW']
    # next time: only the new days of the stored listings, no search
    yahoo.quotes.clear()
    yahoo.histories['STHC.SW'] = yahoo_frame([120.0, 121.0, 122.0])
    prices = adjusted_prices.get_benchmark_adj_prices(account, saved)
    assert prices['STHC'].tolist() == [120.0, 121.0, 122.0]


def test_database_of_version_3_gets_the_new_yahoo_tables(tmp_path, monkeypatch):
    folder = tmp_path / 'db'
    folder.mkdir()
    engine = create_engine(f'sqlite:///{folder / "old.db"}')
    with engine.begin() as connection:
        for statement in [
            'CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)',
            'INSERT INTO schema_version VALUES (1, 3)',
            'CREATE TABLE benchmark_components (account VARCHAR, label VARCHAR, search VARCHAR NOT NULL, '
            'weight FLOAT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (account, label))',
            'CREATE TABLE yahoo_finance_info '
            '(id INTEGER PRIMARY KEY, ticker VARCHAR, quote_type VARCHAR, value VARCHAR)',
            "INSERT INTO yahoo_finance_info (ticker, quote_type, value) VALUES ('HYLD.L', 'currency', 'USD'), "
            "('HYLD.L', 'longName', 'iShares Global High Yield'), ('HYLD.L', 'symbol', 'HYLD.L'), "
            "('HYLD.L', 'shortName', ''), ('VUKE.L', 'currency', 'GBp')",
            'CREATE TABLE yahoo_finance_hist (id INTEGER PRIMARY KEY, ticker VARCHAR, value FLOAT)',
            'CREATE TABLE yahoo_finance_hist_pf_instr (id INTEGER PRIMARY KEY, ticker VARCHAR, value FLOAT)',
        ]:
            connection.execute(text(statement))
    monkeypatch.setattr(tables, 'engine', engine)
    monkeypatch.setattr(sql, 'engine', engine)
    monkeypatch.setattr(sql, 'SessionLocal', sessionmaker(bind=engine, autoflush=False))
    tables.init_db()
    assert migrations.schema_version(engine) == migrations.latest_version()
    names = set(inspect(engine).get_table_names())
    assert {'yahoo_instruments', 'yahoo_prices'} <= names and not names & set(migrations.OLD_YAHOO_TABLES)
    assert 'ticker' in [c['name'] for c in inspect(engine).get_columns('benchmark_components')]
    instruments = sql.query_yahoo_instruments()
    assert instruments.loc['HYLD.L', 'name_long'] == 'iShares Global High Yield'
    assert instruments.loc['HYLD.L', 'name_short'] is None and instruments.loc['VUKE.L', 'currency'] == 'GBp'
    assert (folder / 'old.db.bak-v3').is_file()


def test_preferred_exchange_wins_over_a_closer_name(yahoo):
    isin = 'JE00B1VS3002'
    yahoo.quotes[isin] = [
        {'symbol': '1675.T', 'longname': 'WisdomTree Physical Silver'},  # same name, Tokyo
        {'symbol': 'PHAG.L', 'longname': 'WisdomTree Physical Silver ETC'},
    ]
    yahoo.histories['1675.T'] = yahoo_frame([3000.0])
    yahoo.histories['PHAG.L'] = yahoo_frame([25.0])
    assert history.search_listing(search=isin, ticker='PHAG', name='WisdomTree Physical Silver') == 'PHAG.L'
    # without a listing on a preferred exchange, any exchange
    yahoo.histories['PHAG.L'] = yahoo_frame([])
    assert history.search_listing(search=isin, ticker='PHAG', name='WisdomTree Physical Silver') == '1675.T'


def test_listings_off_the_preferred_exchanges_are_searched_again(yahoo):
    import portfolio_manager.analytics.instruments as instruments

    isin = 'JE00B1VS3002'
    sql.upsert_products(pd.DataFrame({'id': [7], 'isin': [isin], 'symbol': ['PHAG'], 'name': ['Physical Silver']}))
    sql.upsert_degiro_yahoo_map(product_ids=[7, 8], ticker='1675.T')  # product 8 is not in the products table
    sql.upsert_degiro_yahoo_map(product_ids=[9], ticker='IWDC.SW')  # already on a preferred exchange
    yahoo.quotes[isin] = [{'symbol': '1675.T'}, {'symbol': 'PHAG.L'}]
    yahoo.histories['1675.T'] = yahoo_frame([3000.0])
    yahoo.histories['PHAG.L'] = yahoo_frame([25.0])
    assert instruments.research_listings() == [(7, '1675.T', 'PHAG.L')]
    assert sql.query_degiro_yahoo_map([7])[7] == '1675.T'  # dry run
    instruments.research_listings(apply=True)
    assert sql.query_degiro_yahoo_map([7, 9]) == {7: 'PHAG.L', 9: 'IWDC.SW'}
