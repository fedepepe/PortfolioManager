import numpy as np
import pandas as pd
import pytest

import portfolio_manager.analytics.instruments as instruments
import portfolio_manager.storage.queries as sql
from portfolio_manager.analytics.metrics import Metrics
from portfolio_manager.config.accounts import Account, Brokers
from portfolio_manager.dashboard.instruments_data import InstrumentsData
from portfolio_manager.market_data.history import traded_value

CHF = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')
EUR = Account('Portfolio EUR', Brokers.DEGIRO, 'config_2.json', 'EUR')
# 8 months of trading days up to today
DAYS = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=170)


def store(ticker: str, currency: str, close: float, volume: float, days=DAYS) -> int:
    rng = np.random.default_rng(len(ticker))
    closes = close * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    instrument_id = sql.upsert_yahoo_instrument(ticker, {'currency': currency, 'name_long': ticker})
    sql.save_yahoo_prices(instrument_id, pd.DataFrame({'close': closes, 'volume': volume}, index=days))
    return instrument_id


@pytest.fixture
def market(temp_db):
    # exchange rates of the accounts (consistent cross rates) and three listings in USD, pence and CHF
    rates = {'USD/CHF': 0.8, 'GBP/CHF': 1.1, 'EUR/CHF': 0.95, 'EUR/USD': 0.95 / 0.8, 'EUR/GBP': 0.95 / 1.1}
    sql.upsert_currency_pairs({pair: n for n, pair in enumerate(rates, start=1)})
    for n, rate in enumerate(rates.values(), start=1):
        sql.insert_degiro_hist(n, pd.DataFrame({'price': rate}, index=DAYS))
    store('AAA', 'USD', 100.0, 1000.0)  # about 100,000 USD a day
    store('BBB.L', 'GBp', 5000.0, 3000.0)  # about 150,000 GBP a day (prices in pence)
    store('CCC.SW', 'CHF', 50.0, 1000.0)  # about 50,000 CHF a day


def test_traded_value_in_the_currency_of_each_listing(market):
    traded, currencies = traded_value(['AAA', 'BBB.L'])
    assert currencies == {'AAA': 'USD', 'BBB.L': 'GBP'}  # pence converted to pounds
    prices = sql.query_yahoo_prices(['BBB.L'])
    assert traded['BBB.L'].iloc[-1] == pytest.approx(prices['close'].iloc[-1] * 3000.0 / 100)


def test_average_of_the_last_3_calendar_months(market):
    averages = instruments.average_traded_values(['AAA', 'BBB.L', 'CCC.SW'])
    traded, _ = traded_value(['AAA'])
    last_months = traded['AAA'][traded.index > DAYS[-1] - pd.DateOffset(months=3)]
    assert averages.loc['AAA', 'traded_value'] == pytest.approx(last_months.mean())
    assert 60 <= len(last_months) <= 67 and averages.loc['BBB.L', 'currency'] == 'GBP'


def test_listing_without_recent_prices_is_left_out(market):
    store('OLD', 'USD', 10.0, 1e9, days=DAYS[:100])  # very traded, but no price in the last months
    assert 'OLD' not in instruments.average_traded_values(['AAA', 'OLD']).index


def test_ranking_is_the_same_for_every_account_currency(market):
    averages = instruments.average_traded_values(['AAA', 'BBB.L', 'CCC.SW'])
    assert instruments.rank_by_traded_value(CHF, averages) == ['BBB.L', 'AAA', 'CCC.SW']
    assert instruments.rank_by_traded_value(EUR, averages) == ['BBB.L', 'AAA', 'CCC.SW']


def test_catalog_built_into_the_database(market, tmp_path, monkeypatch):
    products = pd.DataFrame({'id': [1, 2, 3, 4], 'isin': ['A', 'B', 'C', 'D']})
    monkeypatch.setattr(instruments, 'catalog_products', lambda: products)
    monkeypatch.setattr(instruments, 'MAX_ETF_CATALOG_SIZE', 2)
    monkeypatch.setattr(instruments, 'RESULTS_DIR', str(tmp_path))
    for product_id, ticker in [(1, 'AAA'), (2, 'BBB.L'), (3, 'CCC.SW')]:
        sql.save_listing_search([product_id], ticker, 'found', 'own line')
    instruments.build_etf_catalog_data(CHF)
    catalog = sql.query_etf_catalog('Portfolio CHF')
    assert catalog['ticker'].tolist() == ['BBB.L', 'AAA'] and catalog['rank'].tolist() == [1, 2]
    assert catalog['currency'].tolist() == ['GBP', 'USD']
    assert catalog['traded_value'].iloc[0] == pytest.approx(150_000, rel=0.2)  # in pounds, not pence or CHF
    # 8 months of history: no 1, 3 or 5-year return yet
    assert catalog[['total_return', 'pa_return', 'volatility', 'sharpe_ratio', 'max_drawdown']].notna().all().all()
    assert catalog[['return_1y', 'return_3y', 'return_5y']].isna().all().all()
    assert (tmp_path / 'Portfolio_CHF_catalog.xlsx').is_file()
    # the Instruments page: the table with labels, and the prices in the account currency
    data = InstrumentsData(CHF)
    assert data.perf_df['Ticker'].tolist() == ['BBB.L', 'AAA'] and data.perf_df['Name'].tolist() == ['BBB.L', 'AAA']
    assert Metrics.SHARPE_RATIO.name in data.perf_df and list(data.adj_close_df.columns) == ['BBB.L', 'AAA']
    last_close = sql.query_yahoo_prices(['BBB.L'])['close'].iloc[-1]
    assert data.adj_close_df['BBB.L'].iloc[-1] == pytest.approx(last_close / 100 * 1.1)  # pence to CHF
    # another build replaces the catalog of the account
    monkeypatch.setattr(instruments, 'MAX_ETF_CATALOG_SIZE', 1)
    instruments.build_etf_catalog_data(CHF)
    assert sql.query_etf_catalog('Portfolio CHF')['ticker'].tolist() == ['BBB.L']


def test_one_listing_per_fund_in_the_catalog(market, tmp_path, monkeypatch):
    # BBB.L (GBP, the most traded) and CCC.SW (CHF) are lines of the same fund; AAA is another fund
    products = pd.DataFrame({'id': [1, 2, 3], 'isin': ['IE00000000A1', 'IE00000000B2', 'IE00000000B2']})
    sql.upsert_products(products)
    monkeypatch.setattr(instruments, 'catalog_products', lambda: products)
    monkeypatch.setattr(instruments, 'RESULTS_DIR', str(tmp_path))
    for product_id, ticker in [(1, 'AAA'), (2, 'BBB.L'), (3, 'CCC.SW')]:
        sql.save_listing_search([product_id], ticker, 'found', 'own line')
    # the CHF account gets the CHF line; the fund keeps the rank of its most traded line
    instruments.build_etf_catalog_data(CHF)
    assert sql.query_etf_catalog('Portfolio CHF')['ticker'].tolist() == ['CCC.SW', 'AAA']
    # no line in EUR: the most traded line
    instruments.build_etf_catalog_data(EUR)
    assert sql.query_etf_catalog('Portfolio EUR')['ticker'].tolist() == ['BBB.L', 'AAA']
    # the ISIN shown is the Degiro one of the fund
    table = instruments.catalog_table(CHF)
    assert table[instruments.InstrPerfTableCols.isin].tolist() == ['IE00000000B2', 'IE00000000A1']
