from collections import Counter
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

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
    # lost: tickers whose next answers get lost (count), as Yahoo answers "no timezone"
    source = SimpleNamespace(histories={}, quotes={}, info={}, isins={}, downloads=[], failing=set(), lost=Counter())

    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker

        @property
        def isin(self):
            return source.isins.get(self.ticker, '-')

        @property
        def history_metadata(self):
            return {'currency': source.info.get(self.ticker, {}).get('currency')}

        def history(self, interval, auto_adjust, actions=False, start=None, end=None, period=None, raise_errors=False):
            source.downloads.append((self.ticker, start))
            # a ticker Yahoo knows: with a history, or returned by a search
            known = self.ticker in source.histories or any(
                q.get('symbol') == self.ticker for quotes in source.quotes.values() for q in quotes
            )
            if raise_errors and (source.lost[self.ticker] > 0 or not known):
                source.lost[self.ticker] -= 1
                raise YFTzMissingError(self.ticker)
            df = source.histories.get(self.ticker, yahoo_frame([]))
            days = df.index.tz_localize(None)
            if start is not None:
                df, days = df[days >= pd.Timestamp(start)], days[days >= pd.Timestamp(start)]
            df = df if end is None else df[days < pd.Timestamp(end)]
            if raise_errors and df.empty:
                raise YFPricesMissingError(self.ticker, '')
            return df

    monkeypatch.setattr(history.yf, 'Ticker', Ticker)
    monkeypatch.setattr(
        history,
        'search_ticker',
        lambda text: None if text in source.failing else SimpleNamespace(quotes=source.quotes.get(text, [])),
    )
    monkeypatch.setattr(history, 'get_ticker_info', lambda ticker: source.info.get(ticker, {}))
    # the Degiro prices of the tests (on DAYS) count as recent
    import portfolio_manager.analytics.instruments as instruments

    monkeypatch.setattr(instruments, 'MAX_DEGIRO_PRICE_AGE', pd.Timestamp.now() - DAYS[0] + pd.Timedelta(days=1))
    monkeypatch.setattr(history, 'MIN_COMMON_DAYS', 1)  # the prices of the tests cover a few days
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
    yahoo.isins['HYLD.L'] = isin
    ticker = history.search_listing(search=isin, ticker='HYLD', name='iShares Global High Yield Corp Bond USD Dist')
    assert ticker == 'HYLD.L'
    stored = sql.query_yahoo_instruments(['HYLD.L']).loc['HYLD.L']
    assert (stored['currency'], stored['quote_type']) == (
        'USD',
        'ETF',
    ) and 'isin' not in stored  # no ISIN from yfinance
    assert history.stored_history('HYLD.L')['close'].tolist() == [85.0, 86.0]
    assert sql.query_yahoo_instruments(['IHYG.MI']).empty  # the other listings are not stored


def test_search_by_ticker_and_preferred_exchange(yahoo):
    # a search text that is not an ISIN finds nothing: the ticker is searched, on any known exchange
    yahoo.quotes['STHC'] = [{'symbol': 'STHC.SW'}, {'symbol': 'STHC.DE'}, {'symbol': 'STHCX'}]
    yahoo.histories['STHC.SW'] = yahoo_frame([120.0])
    yahoo.histories['STHC.DE'] = yahoo_frame([110.0])
    yahoo.histories['STHCX'] = yahoo_frame([1.0])
    assert history.search_listing(search='STHC.SW', ticker='STHC') == 'STHC.DE'  # Frankfurt first
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


def test_ticker_searched_when_the_isin_finds_no_preferred_listing(yahoo):
    isin = 'JE00B1VS3002'
    yahoo.quotes[isin] = [{'symbol': f'{isin}.SG'}, {'symbol': '1675.T'}]  # Stuttgart without prices, Tokyo
    yahoo.quotes['PHPD'] = [{'symbol': 'PHPD.L', 'longname': 'WisdomTree Physical Palladium'}, {'symbol': 'PHPDX'}]
    yahoo.histories['1675.T'] = yahoo_frame([3000.0])
    yahoo.histories['PHPD.L'] = yahoo_frame([150.0])
    assert history.search_listing(search=isin, ticker='PHPD', name='WisdomTree Physical Palladium') == 'PHPD.L'


def test_listing_found_by_ticker_needs_a_similar_name(yahoo):
    # the ISIN finds nothing on a preferred exchange; the ticker belongs to a share on London
    yahoo.quotes['FMET'] = [{'symbol': 'FMET.L', 'longname': 'Fulcrum Metals plc'}]
    yahoo.histories['FMET.L'] = yahoo_frame([10.0])
    assert history.search_listing(search='IE000GETKIK8', ticker='FMET', name='Fidelity Metaverse ETF Acc') is None
    assert history.search_listing(search='IE000GETKIK8', ticker='FMET') == 'FMET.L'  # without a name: no check


def test_own_line_probed_directly_when_the_isin_search_misses_it(yahoo):
    isin = 'IE00B74DQ490'
    yahoo.quotes[isin] = [{'symbol': 'HYLD.L', 'longname': 'iShares Global High Yield Corp Bond UCITS ETF USD'}]
    yahoo.quotes['HYLD.SW'] = [{'symbol': 'HYLD.SW', 'longname': 'iShares Global High Yield Corp Bond UCITS ETF USD'}]
    yahoo.histories['HYLD.L'] = yahoo_frame([85.0])
    yahoo.histories['HYLD.SW'] = yahoo_frame([80.0])
    name = 'iShares Global HY Corp Bond UCITS ETF USD'
    assert history.find_listing(isin, 'HYLD', name, exchange='SW') == history.Listing('HYLD.SW', 'found', 'own line')
    # a listing with the same ticker but another name is not the own line
    yahoo.quotes['HYLD.SW'] = [{'symbol': 'HYLD.SW', 'longname': 'Hyland Software Inc'}]
    searches = history.ListingSearches()
    assert history.find_listing(isin, 'HYLD', name, exchange='SW', searches=searches).ticker == 'HYLD.L'


def test_search_without_answer_is_failed(yahoo):
    yahoo.failing.add('IE00B74DQ490')
    found = history.find_listing('IE00B74DQ490', name='iShares Global HY')
    assert found == history.Listing(None, 'failed', detail='Yahoo search did not answer')
    yahoo.failing.clear()
    assert history.find_listing('IE00B74DQ490', name='iShares Global HY') == history.Listing(
        None, 'not found', detail='no listing'
    )


def products(rows):
    # products of the Degiro catalog: id, isin, symbol, name, exchange (Xetra 194, SIX 947), in EUR
    return pd.DataFrame(rows, columns=['id', 'isin', 'symbol', 'name', 'exchange_id']).assign(currency='EUR')


def degiro_prices(product_id: int, prices: list[float]):
    # stored Degiro prices of a product, on the first days
    sql.insert_degiro_hist(product_id, pd.DataFrame({'price': prices}, index=DAYS[: len(prices)]))


def listing(yahoo, ticker: str, closes: list[float], currency: str = 'EUR'):
    # a Yahoo listing with prices
    yahoo.histories[ticker] = yahoo_frame(closes)
    yahoo.info[ticker] = {'currency': currency}


def test_catalog_has_every_line_of_a_fund(temp_db):
    import portfolio_manager.analytics.instruments as instruments

    # VWRL on Amsterdam, on SIX and on SIX in another currency (same ISIN and symbol), a stock, a non-catalog exchange
    rows = [
        (4586985, 'IE00B3RBWM25', 'VWRL', 200, 'EUR', 'ETF', True),
        (11854704, 'IE00B3RBWM25', 'VWRL', 947, 'CHF', 'ETF', True),
        (11854705, 'IE00B3RBWM25', 'VWRL', 947, 'USD', 'ETF', True),
        (13728386, 'IE00B3RBWM25', 'VWRL', 1000, 'CHF', 'STOCK', False),
        (4626564, 'IE00B3RBWM25', 'VWRL', 570, 'GBP', 'ETF', True),
    ]
    columns = ['id', 'isin', 'symbol', 'exchange_id', 'currency', 'product_type', 'tradable']
    sql.upsert_products(pd.DataFrame(rows, columns=columns))
    assert sorted(instruments.catalog_products()['id']) == [4586985, 11854704, 11854705]


def test_listing_with_a_wrong_currency_is_found_in_its_real_one(yahoo, monkeypatch):
    # VDET on SIX: Yahoo labels the prices CHF, but they are in USD (one USD is 0.80 CHF)
    isin = 'IE00BZ163L38'
    yahoo.quotes[isin] = [{'symbol': 'VDET.SW'}]
    listing(yahoo, 'VDET.SW', [42.0, 42.5], currency='CHF')
    degiro = reference([33.6, 34.0], currency='CHF', rates={'USD': 0.8, 'EUR': 0.93})
    found = history.find_listing(isin, 'VDET', exchange='SW', currency='CHF', reference=degiro)
    assert found.ticker == 'VDET.SW' and found.rule == 'own line (currency fixed)'
    assert found.currency_fix.yahoo_currency == 'CHF' and found.currency_fix.currency == 'USD'
    assert found.currency_fix.gap == pytest.approx(0.0)
    # Yahoo corrects its label: no fix
    yahoo.info['VDET.SW'] = {'currency': 'USD'}
    found = history.find_listing(isin, 'VDET', exchange='SW', currency='CHF', reference=degiro)
    assert found == history.Listing('VDET.SW', 'found', 'own line')
    # prices matching in no currency: another instrument
    listing(yahoo, 'VDET.SW', [50.0, 51.0], currency='CHF')
    assert history.find_listing(isin, 'VDET', exchange='SW', reference=degiro).status == 'not found'
    # too few common days to try other currencies (e.g. a single stale quote): not fixed
    monkeypatch.setattr(history, 'MIN_COMMON_DAYS', 3)
    listing(yahoo, 'VDET.SW', [42.0, 42.5], currency='CHF')
    assert history.find_listing(isin, 'VDET', exchange='SW', reference=degiro).status == 'not found'


def test_currency_fix_saved_by_the_mapping_and_used_for_the_prices(yahoo, tmp_path, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    catalog = products([(1, 'IE00BZ163L38', 'VDET', 'Vanguard USD EM Gov Bond', 947)]).assign(currency='CHF')
    sql.upsert_products(catalog)
    monkeypatch.setattr(instruments, 'catalog_products', lambda: catalog)
    monkeypatch.setattr(instruments, 'traded_product_ids', lambda: set())
    monkeypatch.setattr(instruments, 'RESULTS_DIR', str(tmp_path))
    sql.upsert_currency_pairs({'USD/CHF': 714321})
    sql.insert_degiro_hist(714321, pd.DataFrame({'price': [0.8] * len(DAYS)}, index=DAYS))
    degiro_prices(1, [33.6, 34.0])
    yahoo.quotes['IE00BZ163L38'] = [{'symbol': 'VDET.SW'}]
    listing(yahoo, 'VDET.SW', [42.0, 42.5], currency='CHF')
    sql.upsert_yahoo_instrument('VDET.SW', {'currency': 'CHF'})  # as Yahoo reports it
    assert instruments.map_etf_catalog(degiro_prices=instruments.DegiroPrices()) == Counter({'found': 1})
    fixes = sql.query_currency_fixes()
    assert fixes.loc['VDET.SW', ['yahoo_currency', 'currency', 'product_id']].tolist() == ['CHF', 'USD', 1]
    assert history.listing_currencies(['VDET.SW']) == {'VDET.SW': 'USD'}  # the prices are converted from USD
    assert sql.query_yahoo_instruments(['VDET.SW']).loc['VDET.SW', 'currency'] == 'CHF'  # Yahoo's data unchanged
    review = pd.read_excel(tmp_path / 'yahoo_mapping.xlsx', sheet_name=None)
    assert review['Mapping'].loc[0, 'currency_fixed'] == 'USD' and list(review['Currency fixes']['ticker']) == [
        'VDET.SW'
    ]
    # Yahoo corrects its label: the fix is removed at the next mapping
    yahoo.info['VDET.SW'] = {'currency': 'USD'}
    instruments.map_etf_catalog(remap=True, degiro_prices=instruments.DegiroPrices())
    assert sql.query_currency_fixes().empty
    # a fix whose listing is no longer mapped (the product not found any more) is removed at the end of a mapping
    yahoo.info['VDET.SW'] = {'currency': 'CHF'}
    instruments.map_etf_catalog(remap=True, degiro_prices=instruments.DegiroPrices())
    assert list(sql.query_currency_fixes().index) == ['VDET.SW']
    listing(yahoo, 'VDET.SW', [60.0, 61.0], currency='CHF')  # other prices: not found
    instruments.map_etf_catalog(remap=True, degiro_prices=instruments.DegiroPrices())
    assert sql.query_degiro_yahoo_map() == {} and sql.query_currency_fixes().empty


def test_portfolio_remap_shows_and_saves_a_currency_fix(yahoo, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    sql.upsert_products(products([(3, 'IE00BZ163L38', 'VDET', 'Vanguard USD EM Gov Bond', 947)]).assign(currency='CHF'))
    sql.save_listing_search([3], 'VDET.SW', 'found', 'own line')  # the listing stays, its currency is fixed
    monkeypatch.setattr(instruments, 'traded_product_ids', lambda: {3})
    sql.upsert_currency_pairs({'USD/CHF': 714321})
    sql.insert_degiro_hist(714321, pd.DataFrame({'price': [0.8] * len(DAYS)}, index=DAYS))
    degiro_prices(3, [33.6, 34.0])
    yahoo.quotes['IE00BZ163L38'] = [{'symbol': 'VDET.SW'}]
    listing(yahoo, 'VDET.SW', [42.0, 42.5], currency='CHF')
    changes = instruments.remap_portfolio_listings()
    assert changes[['stored', 'found', 'rule', 'currency']].values.tolist() == [
        ['VDET.SW', 'VDET.SW', 'own line (currency fixed)', 'USD']
    ]
    assert sql.query_currency_fixes().empty  # shown only
    instruments.remap_portfolio_listings(apply=True)
    assert sql.query_currency_fixes()['currency'].to_dict() == {'VDET.SW': 'USD'}
    assert instruments.remap_portfolio_listings().empty  # nothing left to change


def test_listing_with_too_few_common_days_is_rejected(yahoo, monkeypatch):
    # a Hamburg line with a single quote in the period: the same price, but too little to compare (and to use)
    yahoo.quotes['GB00BM9JYJ86'] = [{'symbol': 'LI0X.HM'}]
    listing(yahoo, 'LI0X.HM', [10.0])
    degiro = reference([10.0, 10.1, 10.2])
    assert history.find_listing('GB00BM9JYJ86', 'LI0X', exchange='DE', reference=degiro).ticker == 'LI0X.HM'
    monkeypatch.setattr(history, 'MIN_COMMON_DAYS', 3)
    found = history.find_listing('GB00BM9JYJ86', 'LI0X', exchange='DE', reference=degiro)
    assert found == history.Listing(None, 'not found', detail='LI0X.HM only 1 common days, LI0X.DE no prices')


def test_found_listings_checked_again_after_a_quarter(yahoo):
    import portfolio_manager.analytics.instruments as instruments

    sql.save_listing_search([1], 'AAA.DE', 'found', 'own line')
    sql.save_listing_search([2], 'BBB.DE', 'found', 'own line')
    old = (pd.Timestamp.now() - pd.DateOffset(months=4)).to_pydatetime()
    with sql.engine.begin() as connection:
        connection.execute(text('UPDATE degiro_yahoo_map SET searched_at = :t WHERE product_id = 1'), {'t': old})
    df = products([(n, f'XX{n}', f'S{n}', f'ETF {n}', 194) for n in (1, 2)])
    assert instruments.products_to_search(df)['id'].tolist() == []
    assert instruments.products_to_search(df, remap_after=instruments.REMAP_PERIOD)['id'].tolist() == [1]


def test_which_products_are_searched(yahoo):
    import portfolio_manager.analytics.instruments as instruments

    sql.save_listing_search([2], 'BBB.DE', 'found', 'own line')
    sql.save_listing_search([3], None, 'failed')
    sql.save_listing_search([4], None, 'not found')
    sql.save_listing_search([5], None, 'not found')
    old = (pd.Timestamp.now() - instruments.PRODUCT_SEARCH_RETRY - pd.Timedelta(days=1)).to_pydatetime()
    with sql.engine.begin() as connection:
        connection.execute(text('UPDATE degiro_yahoo_map SET searched_at = :t WHERE product_id = 5'), {'t': old})
    df = products([(n, f'XX{n}', f'S{n}', f'ETF {n}', 194) for n in range(1, 6)])
    assert instruments.products_to_search(df)['id'].tolist() == [1, 3, 5]  # never searched, failed, not found long ago
    assert instruments.products_to_search(df, retry_not_found=True)['id'].tolist() == [1, 3, 4, 5]
    assert instruments.products_to_search(df, remap=True)['id'].tolist() == [1, 2, 3, 5]
    # resuming a remap: the found products searched before the given time only
    before = pd.Timestamp.now() + pd.Timedelta(minutes=1)
    assert instruments.products_to_search(df, remap_before=before)['id'].tolist() == [1, 2, 3, 5]
    assert instruments.products_to_search(df, remap_before=before - pd.Timedelta(days=1))['id'].tolist() == [1, 3, 5]


def test_mapping_of_the_catalog(yahoo, tmp_path, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    catalog = products(
        [
            (1, 'IE00B4L5Y983', 'EUNL', 'iShares Core MSCI World', 194),
            (2, 'IE00B4L5Y983', 'SWDA', 'iShares Core MSCI World', 947),  # same fund on SIX: its own line
            (3, 'IE00B74DQ490', 'HYLD', 'iShares Global HY', 947),  # traded in an account: kept
            (4, 'XX0000000000', 'NONE', 'Unknown ETF', 194),
            (5, 'IE00BZ56RN96', 'FAIL', 'Failing ETF', 194),
        ]
    )
    sql.upsert_products(catalog)
    monkeypatch.setattr(instruments, 'catalog_products', lambda: catalog)
    monkeypatch.setattr(instruments, 'traded_product_ids', lambda: {3})
    monkeypatch.setattr(instruments, 'RESULTS_DIR', str(tmp_path))
    yahoo.quotes['IE00B4L5Y983'] = [{'symbol': 'EUNL.DE'}, {'symbol': 'SWDA.SW'}, {'symbol': 'IWDA.L'}]
    for ticker in ['EUNL.DE', 'SWDA.SW', 'IWDA.L']:
        listing(yahoo, ticker, [90.0])
    for product_id in [1, 2, 4, 5]:
        degiro_prices(product_id, [90.0])
    yahoo.failing.update({'IE00BZ56RN96', 'FAIL', 'FAIL.DE'})
    sql.save_listing_search([5], 'OLD.DE', 'found', 'name')
    found = instruments.map_etf_catalog(remap=True, degiro_prices=instruments.DegiroPrices())
    assert found == Counter({'found': 2, 'not found': 1, 'failed': 1})
    # a failed search keeps the listing found before
    assert sql.query_degiro_yahoo_map() == {1: 'EUNL.DE', 2: 'SWDA.SW', 5: 'OLD.DE'}
    review = pd.read_excel(tmp_path / 'yahoo_mapping.xlsx')
    assert sorted(review['product_id']) == [1, 2, 4, 5] and set(review['rule'].dropna()) == {'own line', 'name'}


def test_remap_of_the_portfolio_products(yahoo, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    sql.upsert_products(products([(3, 'IE00B74DQ490', 'HYLD', 'iShares Global HY Corp Bond', 947)]))
    sql.save_listing_search([3], 'HYLD.L', 'found', 'preferred exchange')
    monkeypatch.setattr(instruments, 'traded_product_ids', lambda: {3})
    yahoo.quotes['IE00B74DQ490'] = [{'symbol': 'HYLD.L'}, {'symbol': 'HYLD.SW'}]
    listing(yahoo, 'HYLD.L', [85.0, 86.0])  # 6% off the Degiro prices
    listing(yahoo, 'HYLD.SW', [80.0, 81.0])
    degiro_prices(3, [80.0, 81.0])
    changes = instruments.remap_portfolio_listings()
    assert changes[['product_id', 'stored', 'found', 'rule']].values.tolist() == [[3, 'HYLD.L', 'HYLD.SW', 'own line']]
    assert sql.query_degiro_yahoo_map() == {3: 'HYLD.L'}  # shown only
    instruments.remap_portfolio_listings(apply=True)
    assert sql.query_degiro_yahoo_map() == {3: 'HYLD.SW'}
    assert history.stored_history('HYLD.SW')['close'].tolist() == [80.0, 81.0]


def test_catalog_history_of_the_mapped_listings(yahoo, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    catalog = products([(1, 'IE00B4L5Y983', 'EUNL', 'iShares Core MSCI World', 194), (2, 'XX1', 'X', 'X', 194)])
    sql.upsert_products(catalog)
    monkeypatch.setattr(instruments, 'catalog_products', lambda: catalog)
    monkeypatch.setattr(instruments.time, 'sleep', lambda seconds: None)
    sql.save_listing_search([1], 'EUNL.DE', 'found', 'own line')
    sql.save_listing_search([2], None, 'not found')
    yahoo.histories['EUNL.DE'] = yahoo_frame([90.0, 91.0])
    yahoo.info['EUNL.DE'] = {'currency': 'EUR', 'longName': 'iShares Core MSCI World UCITS ETF'}
    instruments.fetch_etf_catalog_data()
    assert history.stored_history('EUNL.DE')['close'].tolist() == [90.0, 91.0]
    assert sql.query_yahoo_instruments(['EUNL.DE']).loc['EUNL.DE', 'currency'] == 'EUR'
    assert sql.query_listing_isins(['EUNL.DE']) == {'EUNL.DE': 'IE00B4L5Y983'}  # the Degiro ISIN, from the mapping


def test_database_of_version_7_loses_the_yahoo_isin(tmp_path, monkeypatch):
    folder = tmp_path / 'db'
    folder.mkdir()
    engine = create_engine(f'sqlite:///{folder / "old.db"}')
    with engine.begin() as connection:
        for statement in [
            'CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)',
            'INSERT INTO schema_version VALUES (1, 7)',
            'CREATE TABLE yahoo_instruments (id INTEGER PRIMARY KEY, ticker VARCHAR NOT NULL UNIQUE, isin VARCHAR, '
            'name_short VARCHAR, name_long VARCHAR, currency VARCHAR, quote_type VARCHAR, exchange VARCHAR, '
            'market VARCHAR, region VARCHAR)',
            "INSERT INTO yahoo_instruments (ticker, isin, currency) VALUES ('VFEM.AS', 'US63969G2599', 'EUR')",
        ]:
            connection.execute(text(statement))
    monkeypatch.setattr(tables, 'engine', engine)
    monkeypatch.setattr(sql, 'engine', engine)
    monkeypatch.setattr(sql, 'SessionLocal', sessionmaker(bind=engine, autoflush=False))
    tables.init_db()
    columns = [c['name'] for c in inspect(engine).get_columns('yahoo_instruments')]
    assert 'isin' not in columns and 'currency' in columns
    assert sql.query_yahoo_instruments(['VFEM.AS']).loc['VFEM.AS', 'currency'] == 'EUR'


def test_database_of_version_4_keeps_its_listings(tmp_path, monkeypatch):
    folder = tmp_path / 'db'
    folder.mkdir()
    engine = create_engine(f'sqlite:///{folder / "old.db"}')
    with engine.begin() as connection:
        for statement in [
            'CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)',
            'INSERT INTO schema_version VALUES (1, 4)',
            'CREATE TABLE degiro_yahoo_map (product_id INTEGER NOT NULL PRIMARY KEY, ticker VARCHAR NOT NULL)',
            "INSERT INTO degiro_yahoo_map VALUES (1, 'EUNL.DE'), (2, 'HYLD.L')",
        ]:
            connection.execute(text(statement))
    monkeypatch.setattr(tables, 'engine', engine)
    monkeypatch.setattr(sql, 'engine', engine)
    monkeypatch.setattr(sql, 'SessionLocal', sessionmaker(bind=engine, autoflush=False))
    tables.init_db()
    assert migrations.schema_version(engine) == migrations.latest_version()
    searches = sql.query_listing_searches().set_index('product_id')
    assert searches['ticker'].to_dict() == {1: 'EUNL.DE', 2: 'HYLD.L'} and set(searches['status']) == {'found'}
    sql.save_listing_search([3], None, 'not found')  # the listing may be missing now


def reference(prices: list[float], currency: str = 'EUR', rates: dict | None = None) -> history.PriceReference:
    # Degiro prices on the first days, with constant exchange rates of other currencies to theirs
    rates = rates or {}
    return history.PriceReference(
        prices=pd.Series(prices, index=DAYS[: len(prices)]),
        currency=currency,
        rate=lambda other: pd.Series(rates[other], index=DAYS) if other in rates else None,
    )


def test_own_line_with_another_name_is_checked_on_its_prices(yahoo):
    # SPYI on Xetra: Yahoo names its own line differently; the ISIN search returns only a London line in USD
    isin = 'IE00B3YLTY66'
    yahoo.quotes[isin] = [{'symbol': 'IMID.L', 'longname': 'SPDR MSCI ACWI IMI UCITS ETF'}]
    yahoo.quotes['SPYI.DE'] = [{'symbol': 'SPYI.DE', 'longname': 'State Street SPDR MSCI All Country World Investable'}]
    listing(yahoo, 'IMID.L', [216.0, 218.0], currency='USD')
    listing(yahoo, 'SPYI.DE', [180.0, 181.0])
    args = (isin, 'SPYI', 'SPDR MSCI ACWI IMI')
    usd = {'USD': 0.8333}
    found = history.find_listing(*args, exchange='DE', currency='EUR', reference=reference([180.0, 181.5], rates=usd))
    assert found == history.Listing('SPYI.DE', 'found', 'own line')
    # the own line with other prices is another instrument: the London line, converted to EUR, matches
    yahoo.histories['SPYI.DE'] = yahoo_frame([9.0, 9.1])
    found = history.find_listing(*args, exchange='DE', currency='EUR', reference=reference([180.0, 181.5], rates=usd))
    assert found == history.Listing('IMID.L', 'found', 'preferred exchange')
    # without an exchange rate for its currency, the London line cannot be compared
    assert history.find_listing(*args, exchange='DE', reference=reference([180.0, 181.5])).status == 'not found'


def test_price_check_uses_the_median_daily_gap(yahoo):
    listing(yahoo, 'AAA.DE', [100.0, 150.0, 100.5])  # one wrong day
    searches = history.ListingSearches()
    assert searches.price_gap('AAA.DE', reference([100.0, 100.0, 100.0])) == pytest.approx(0.005)
    assert (
        history.find_listing('XX', 'AAA', exchange='DE', reference=reference([100.0, 100.0, 100.0])).ticker == 'AAA.DE'
    )
    # 3% off on every day: another instrument, logged with its gap
    found = history.find_listing('XX', 'AAA', exchange='DE', reference=reference([103.0, 154.5, 103.5]))
    assert found == history.Listing(None, 'not found', detail='AAA.DE gap 2.9%')


def test_ticker_search_listings_with_another_symbol(yahoo):
    # the ISIN search finds only a Stuttgart line without prices; the ticker search finds the SIX line under another
    # symbol, and a coin pair and a US fund with other prices
    isin = 'CH1146882316'
    yahoo.quotes[isin] = [{'symbol': f'{isin}.SG'}]
    yahoo.quotes['WMEG'] = [{'symbol': 'WMEG-USD'}, {'symbol': 'WMEGA.SW'}, {'symbol': 'WMEGX'}]
    listing(yahoo, 'WMEGA.SW', [12.0, 12.1])
    listing(yahoo, 'WMEG-USD', [0.5, 0.6])
    listing(yahoo, 'WMEGX', [30.0, 30.5])
    found = history.find_listing(isin, 'WMEG', exchange='DE', reference=reference([12.0, 12.1]))
    assert found == history.Listing('WMEGA.SW', 'found', 'symbol search')


def test_listing_whose_prices_get_lost(yahoo):
    # GOLD.AS returned by the ISIN search: a lost answer is downloaded again, two make the search fail
    yahoo.quotes['FR0013416716'] = [{'symbol': 'GOLD.AS'}]
    listing(yahoo, 'GOLD.AS', [150.0, 151.0], currency='USD')
    args = ('FR0013416716', 'GOLD', 'Amundi Physical Gold ETC')
    yahoo.lost['GOLD.AS'] = 1
    found = history.find_listing(*args, exchange='AS', reference=reference([150.0, 151.0], currency='USD'))
    assert found == history.Listing('GOLD.AS', 'found', 'own line')
    yahoo.lost['GOLD.AS'] = 2
    with pytest.raises(history.ListingDownloadError):
        history.find_listing(*args, exchange='AS', reference=reference([150.0, 151.5], currency='USD'))
    # a guessed ticker Yahoo does not know (the own line on Xetra) has no prices, nothing failed
    found = history.find_listing(*args, exchange='DE', reference=reference([150.0, 151.5], currency='USD'))
    assert found == history.Listing('GOLD.AS', 'found', 'preferred exchange')


def test_prices_in_pence_are_compared_in_pounds(yahoo):
    listing(yahoo, 'VUKE.L', [1000.0, 1010.0], currency='GBp')
    searches = history.ListingSearches()
    assert searches.price_gap('VUKE.L', reference([12.0, 12.12], rates={'GBP': 1.2})) == pytest.approx(0.0)


def test_listing_is_chosen_on_prices_not_on_the_isin_of_yfinance(yahoo):
    isin = 'IE00B3VVMM84'  # Vanguard FTSE Emerging Markets on Amsterdam
    yahoo.quotes[isin] = [{'symbol': 'VFEM.AS'}]
    listing(yahoo, 'VFEM.AS', [60.0, 60.5])
    yahoo.isins['VFEM.AS'] = 'US63969G2599'  # yfinance's ISIN (a Business Insider name search) of another security
    found = history.find_listing(isin, 'VFEM', exchange='AS', reference=reference([60.0, 60.5]))
    assert found == history.Listing('VFEM.AS', 'found', 'own line')
    # the US Fidelity bitcoin fund FBTC for the Fidelity bitcoin ETP on Xetra: other prices, rejected on them
    yahoo.quotes['FBTC'] = [{'symbol': 'FBTC', 'longname': 'Fidelity Wise Origin Bitcoin Fund'}]
    listing(yahoo, 'FBTC', [80.0, 81.0], currency='USD')
    found = history.find_listing(
        'XS2434891219', 'FBTC', exchange='DE', reference=reference([10.0, 10.1], rates={'USD': 1.0})
    )
    assert found == history.Listing(None, 'not found', detail='FBTC.DE no prices, FBTC gap 701.0%')


def test_product_without_degiro_prices_is_not_found(yahoo, tmp_path, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    catalog = products([(1, 'IE00B4L5Y983', 'EUNL', 'iShares Core MSCI World', 194)])
    sql.upsert_products(catalog)
    monkeypatch.setattr(instruments, 'catalog_products', lambda: catalog)
    monkeypatch.setattr(instruments, 'traded_product_ids', lambda: set())
    monkeypatch.setattr(instruments, 'RESULTS_DIR', str(tmp_path))
    yahoo.quotes['IE00B4L5Y983'] = [{'symbol': 'EUNL.DE'}]
    listing(yahoo, 'EUNL.DE', [90.0])
    assert instruments.map_etf_catalog(degiro_prices=instruments.DegiroPrices()) == Counter({'no Degiro prices': 1})
    searches = sql.query_listing_searches().set_index('product_id')
    assert searches.loc[1, ['ticker', 'status', 'rule']].tolist() == [None, 'not found', 'no Degiro prices']


def test_product_with_old_degiro_prices_is_not_searched(yahoo, tmp_path, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    catalog = products([(1, 'IE00B4L5Y983', 'EUNL', 'iShares Core MSCI World', 194)])
    monkeypatch.setattr(instruments, 'catalog_products', lambda: catalog)
    monkeypatch.setattr(instruments, 'traded_product_ids', lambda: set())
    monkeypatch.setattr(instruments, 'RESULTS_DIR', str(tmp_path))
    yahoo.quotes['IE00B4L5Y983'] = [{'symbol': 'EUNL.DE'}]
    listing(yahoo, 'EUNL.DE', [90.0, 91.0])
    degiro_prices(1, [90.0, 91.0])
    # the last Degiro price is older than MAX_DEGIRO_PRICE_AGE: no longer traded, Yahoo is not asked
    monkeypatch.setattr(instruments, 'MAX_DEGIRO_PRICE_AGE', pd.Timestamp.now() - DAYS[1] - pd.Timedelta(days=1))
    assert instruments.map_etf_catalog(degiro_prices=instruments.DegiroPrices()) == Counter(
        {'no recent Degiro prices': 1}
    )
    assert yahoo.downloads == []
    found = instruments.search_product_listing(next(catalog.itertuples()), degiro_prices=instruments.DegiroPrices())
    assert found.detail == f'Degiro prices to {DAYS[1].date()}'


def test_refused_degiro_chart_is_counted_apart(yahoo, tmp_path, monkeypatch):
    import portfolio_manager.analytics.instruments as instruments

    catalog = products([(1, 'NL0009272756', 'TMX', 'VanEck AMX UCITS ETF', 200)])
    catalog = catalog.assign(vwd_id=327414, vwd_identifier_type='issueid')
    monkeypatch.setattr(instruments, 'catalog_products', lambda: catalog)
    monkeypatch.setattr(instruments, 'traded_product_ids', lambda: set())
    monkeypatch.setattr(instruments, 'RESULTS_DIR', str(tmp_path))
    degiro = instruments.DegiroPrices()
    # Degiro answers with an error for each series
    error = {'error': 'Access denied for issueid:327414', 'type': 'error'}
    degiro.chart_fetcher = SimpleNamespace(
        get_chart=lambda chart_request, raw: {'requestid': '1', 'resolution': 'P1D', 'series': [error] * 3}
    )
    assert instruments.map_etf_catalog(degiro_prices=degiro) == Counter({'Degiro chart refused': 1})
    listing = instruments.search_product_listing(next(catalog.itertuples()), degiro_prices=degiro)
    assert listing.detail == 'Degiro chart refused: Access denied for issueid:327414'


def test_degiro_chart_without_the_series_in_error():
    from portfolio_manager.degiro.charts import fetch_chart

    price = {'expires': '2026-01-08T00:00:00', 'id': 'price:issueid:1', 'type': 'time', 'times': '2026-01-05/P1D'}
    price['data'] = [[0, 10.0], [1, 10.5]]
    response = {'requestid': '1', 'resolution': 'P1D', 'start': '2026-01-05', 'end': '2026-01-07'}
    response['series'] = [price, {'error': 'Access denied for volume', 'type': 'error'}]
    chart = fetch_chart(SimpleNamespace(get_chart=lambda chart_request, raw: response), 1)
    assert chart['price'].tolist() == [10.0, 10.5] and chart.index[0] == pd.Timestamp('2026-01-05')


def test_degiro_prices_of_the_last_3_months(temp_db):
    import portfolio_manager.analytics.instruments as instruments

    days = pd.bdate_range('2025-10-01', '2026-03-31')
    sql.insert_degiro_hist(1, pd.DataFrame({'price': range(len(days))}, index=days))
    sql.upsert_currency_pairs({'EUR/USD': 705366})
    sql.insert_degiro_hist(705366, pd.DataFrame({'price': [1.25] * len(days)}, index=days))
    product = SimpleNamespace(id=1, currency='USD', vwd_id=None, vwd_identifier_type=None)
    found = instruments.DegiroPrices().reference(product)
    assert found.prices.index[0] > days[-1] - pd.DateOffset(months=3) > days[0] and found.prices.index[-1] == days[-1]
    assert found.currency == 'USD' and found.rate('EUR').iloc[-1] == pytest.approx(1.25)  # one EUR in USD
    assert instruments.DegiroPrices().reference(SimpleNamespace(id=2, currency='EUR', vwd_id=None)) is None


def test_listings_in_the_currency_of_the_product_first(yahoo):
    isin = 'DE000A28M8D0'
    yahoo.quotes[isin] = [
        {'symbol': 'VBTC.SW', 'longname': 'VanEck Bitcoin ETN'},  # exact name, but in CHF
        {'symbol': 'VBTC.DE', 'longname': 'VanEck Bitcoin ETN A'},
    ]
    yahoo.histories['VBTC.SW'] = yahoo_frame([30.0])
    yahoo.histories['VBTC.DE'] = yahoo_frame([32.0])
    yahoo.info['VBTC.SW'] = {'currency': 'CHF'}
    yahoo.info['VBTC.DE'] = {'currency': 'EUR'}
    found = history.find_listing(isin, isin, 'VanEck Bitcoin ETN', exchange='AS', currency='EUR')
    assert found == history.Listing('VBTC.DE', 'found', 'currency')
    # without a listing in the currency of the product: the closest name
    assert history.find_listing(isin, isin, 'VanEck Bitcoin ETN', exchange='AS', currency='USD').ticker == 'VBTC.SW'


def test_equal_names_follow_the_preferred_exchange_order(yahoo):
    isin = 'CH0593331561'
    yahoo.quotes[isin] = [{'symbol': f'{isin}.SG', 'longname': '21Shares Polkadot ETP'}]
    yahoo.quotes[isin].insert(0, {'symbol': 'ADOT.MI', 'longname': '21Shares Polkadot ETP'})
    yahoo.histories['ADOT.MI'] = yahoo_frame([10.0])
    yahoo.histories[f'{isin}.SG'] = yahoo_frame([10.0])
    assert history.find_listing(isin, 'PDOT', '21Shares Polkadot ETP', exchange='DE').ticker == 'ADOT.MI'  # Milan first


def test_remap_of_the_products_not_on_their_own_line(yahoo):
    import portfolio_manager.analytics.instruments as instruments

    sql.save_listing_search([1], 'AAA.DE', 'found', 'own line')
    sql.save_listing_search([2], 'BBB.L', 'found', 'preferred exchange')
    sql.save_listing_search([3], 'CCC.DE', 'found', 'own line (confirmed)')
    df = products([(n, f'XX{n}', f'S{n}', f'ETF {n}', 194) for n in range(1, 4)])
    assert instruments.products_to_search(df, remap_found_not_own_line=True)['id'].tolist() == [2]


def test_database_of_version_5_gets_the_isin_of_its_products(tmp_path, monkeypatch):
    folder = tmp_path / 'db'
    folder.mkdir()
    engine = create_engine(f'sqlite:///{folder / "old.db"}')
    with engine.begin() as connection:
        for statement in [
            'CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)',
            'INSERT INTO schema_version VALUES (1, 5)',
            'CREATE TABLE degiro_yahoo_map (product_id INTEGER NOT NULL PRIMARY KEY, ticker VARCHAR, '
            'status VARCHAR NOT NULL, rule VARCHAR, searched_at DATETIME)',
            "INSERT INTO degiro_yahoo_map VALUES (1, 'EUNL.DE', 'found', 'own line', NULL)",
            "INSERT INTO degiro_yahoo_map VALUES (2, NULL, 'not found', NULL, NULL)",
            'CREATE TABLE products (id INTEGER NOT NULL PRIMARY KEY, isin VARCHAR)',
            "INSERT INTO products VALUES (1, 'IE00B4L5Y983')",
        ]:
            connection.execute(text(statement))
    monkeypatch.setattr(tables, 'engine', engine)
    monkeypatch.setattr(sql, 'engine', engine)
    monkeypatch.setattr(sql, 'SessionLocal', sessionmaker(bind=engine, autoflush=False))
    tables.init_db()
    columns = [c['name'] for c in inspect(engine).get_columns('degiro_yahoo_map')]
    assert columns == ['product_id', 'isin', 'ticker', 'status', 'rule', 'searched_at']  # no own line columns
    searches = sql.query_listing_searches().set_index('product_id')
    assert searches['isin'].to_dict() == {1: 'IE00B4L5Y983', 2: None}
    assert sql.query_degiro_yahoo_map() == {1: 'EUNL.DE'}
