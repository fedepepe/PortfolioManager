import numpy as np
import pandas as pd
import pytest

import portfolio_manager.analytics.saa as saa
import portfolio_manager.storage.queries as sql
from portfolio_manager.analytics.metrics import Metrics
from portfolio_manager.config.accounts import Account, Brokers

CHF = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')
EUR = Account('Portfolio EUR', Brokers.DEGIRO, 'config_2.json', 'EUR')
DAYS = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=400)  # recent prices, more than a year


def store(ticker: str, currency: str, close: float, volume: float) -> int:
    rng = np.random.default_rng(len(ticker) + int(volume))
    closes = close * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(DAYS))))
    instrument_id = sql.upsert_yahoo_instrument(ticker, {'currency': currency, 'name_long': f'{ticker} fund'})
    sql.save_yahoo_prices(instrument_id, pd.DataFrame({'close': closes, 'volume': volume}, index=DAYS))
    return instrument_id


def classify(rows: list[tuple]) -> None:
    # etf_classes: isin, name, asset class, sub-class, region, leveraged, hedged
    columns = ['isin', 'name', 'asset_class', 'sub_class', 'region', 'leveraged', 'hedged']
    df = pd.DataFrame(rows, columns=columns).assign(source='degiro', classified_at=pd.Timestamp.now())
    sql.save_etf_classes(df)


@pytest.fixture
def universe(temp_db):
    # exchange rates of the CHF account; Swiss equity on SIX (CHF) and Xetra (EUR), a global fund, a leveraged one
    sql.upsert_currency_pairs({'EUR/CHF': 1, 'USD/CHF': 2})
    sql.insert_degiro_hist(1, pd.DataFrame({'price': 0.95}, index=DAYS))
    sql.insert_degiro_hist(2, pd.DataFrame({'price': 0.8}, index=DAYS))
    classify(
        [
            ('CH0237935652', 'iShares Core SPI', 'Equity', 'Broad', 'Switzerland', False, False),
            ('CH0008899764', 'iShares SMI', 'Equity', 'Broad', 'Switzerland', False, False),
            ('IE00BK5BQT80', 'Vanguard FTSE All-World', 'Equity', 'Broad', 'Global', False, False),
            ('XX0000000001', 'Swiss 3x Leveraged', 'Equity', 'Broad', 'Switzerland', True, False),
        ]
    )
    products = pd.DataFrame(
        [
            (1, 'CH0237935652', 'CHSPI', 947),
            (2, 'CH0237935652', 'SPICHA', 194),  # the same fund on Xetra, in EUR
            (3, 'CH0008899764', 'CSSMI', 947),
            (4, 'IE00BK5BQT80', 'VWCE', 194),
            (5, 'XX0000000001', 'LEV3', 947),
        ],
        columns=['id', 'isin', 'symbol', 'exchange_id'],
    )
    sql.upsert_products(products)
    for product_id, ticker in [(1, 'CHSPI.SW'), (2, 'SPIC.DE'), (3, 'CSSMI.SW'), (4, 'VWCE.DE'), (5, 'LEV3.SW')]:
        sql.save_listing_search([product_id], ticker, 'found', 'own line')
    store('CHSPI.SW', 'CHF', 150.0, 10_000)  # 1.5 M CHF a day
    store('CSSMI.SW', 'CHF', 120.0, 20_000)  # 2.4 M CHF a day: the most traded
    store('SPIC.DE', 'EUR', 160.0, 30_000)  # 4.8 M EUR = 4.56 M CHF a day, but not in CHF
    store('VWCE.DE', 'EUR', 130.0, 50_000)
    store('LEV3.SW', 'CHF', 10.0, 900_000)


def test_candidates_of_a_bucket_in_the_account_currency(universe):
    candidates = saa.bucket_candidates(CHF, 'Equity', 'region', 'Switzerland')
    assert candidates['ticker'].tolist() == ['CSSMI.SW', 'CHSPI.SW']  # by traded value; no EUR line, no leveraged
    assert candidates['traded_value'].iloc[0] == pytest.approx(2.4e6, rel=0.2)
    assert candidates['name'].iloc[0] == 'CSSMI.SW fund' and candidates['currency'].tolist() == ['CHF', 'CHF']
    # performance metrics in the account currency (400 days of history: no 3-year return yet)
    shown = [m.name for m in saa.CANDIDATE_METRICS if m != Metrics.ANN_3Y_RETURN]
    assert candidates[shown].notna().all().all() and candidates[Metrics.ANN_3Y_RETURN.name].isna().all()
    assert candidates[Metrics.VOLATILITY.name].iloc[0] == pytest.approx(0.16, rel=0.3)


def test_candidates_in_other_currencies_and_leveraged_on_request(universe):
    candidates = saa.bucket_candidates(CHF, 'Equity', 'region', 'Switzerland', account_currency_only=False)
    assert candidates['ticker'].tolist() == ['SPIC.DE', 'CSSMI.SW', 'CHSPI.SW']  # EUR value converted to CHF
    assert candidates['traded_value'].iloc[0] == pytest.approx(4.8e6 * 0.95, rel=0.2)
    leveraged = saa.bucket_candidates(CHF, 'Equity', 'region', 'Switzerland', include_leveraged=True)
    assert leveraged['ticker'].iloc[0] == 'LEV3.SW'


def test_candidates_of_a_class_not_split_searched_and_limited(universe):
    candidates = saa.bucket_candidates(EUR, 'Equity', account_currency_only=False)
    assert candidates['ticker'].tolist()[:2] == ['VWCE.DE', 'SPIC.DE'] and len(candidates) == 4
    assert saa.bucket_candidates(EUR, 'Equity', account_currency_only=False, limit=2)['ticker'].tolist() == [
        'VWCE.DE',
        'SPIC.DE',
    ]
    # search by name, ISIN or ticker
    found = saa.bucket_candidates(CHF, 'Equity', account_currency_only=False, search='smi')
    assert found['ticker'].tolist() == ['CSSMI.SW']
    assert saa.bucket_candidates(CHF, 'Equity', search='CH02379')['ticker'].tolist() == ['CHSPI.SW']
    assert saa.bucket_candidates(CHF, 'Fixed Income').empty  # no fund of this class


def test_split_values_with_the_home_region_first(universe):
    assert saa.split_values(CHF, 'Equity', 'region').to_dict() == {'Switzerland': 2, 'Global': 1}  # not the leveraged
    assert saa.split_values(EUR, 'Equity', 'region').index.tolist() == ['Switzerland', 'Global']  # by number of funds
    assert saa.split_values(CHF, 'Equity', 'sub_class').to_dict() == {'Broad': 3}
