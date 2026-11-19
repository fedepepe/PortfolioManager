from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import portfolio_manager.analytics.price_updates as updates
import portfolio_manager.market_data.history as history
import portfolio_manager.market_data.rates as rates
import portfolio_manager.storage.queries as sql
from portfolio_manager.config.markets import DEFAULT_MARKET_INSTRUMENTS
from portfolio_manager.config.taa import (
    DEFAULT_RATE_SERIES,
    DEFAULT_TAA_INSTRUMENTS,
    RateSeries,
    RateSources,
    TaaInstrument,
)
from portfolio_manager.storage.models import init_db

ECB_CSV = """KEY,FREQ,BENCHMARK_ITEM,DATA_TYPE_EST,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EST.B.EU000A2X2A25.WT,B,EU000A2X2A25,WT,2026-11-16,2.441,A
EST.B.EU000A2X2A25.WT,B,EU000A2X2A25,WT,2026-11-17,2.442,A
"""
# with the byte order mark and the publication details of the SNB files; items with two dimensions
SNB_CSV = (
    '﻿"CubeId";"rendeiduebd"\n"PublishingDate";"2026-11-02 14:30"\n\n"Date";"D0";"D1";"Value"\n'
    '"2026-10-29";"CHF";"10J";"0.51"\n"2026-10-29";"EUR";"10J";"2.9"\n"2026-10-30";"CHF";"10J";"0.505"\n'
    '"2026-10-30";"CHF";"2J";""\n'
)
BOE_CSV = 'DATE,IUDSOIA\n14 Nov 2026,3.9833\n17 Nov 2026,3.984\n'
BIS_CSV = 'FREQ,REF_AREA,UNIT_MEASURE,TIME_PERIOD,OBS_VALUE\nD,JP,368,2026-11-16,1.25\nD,JP,368,2026-11-17,1.25\n'

ESTR = RateSeries('EUR_ESTR', '€STR', 'EUR', 'O/N', RateSources.ECB, 'EST/B.EU000A2X2A25.WT')
CHF_10Y = RateSeries('CHF_GOV_10Y', 'Swiss 10Y', 'CHF', '10Y', RateSources.SNB, 'rendeiduebd/CHF/10J')
SONIA = RateSeries('GBP_SONIA', 'SONIA', 'GBP', 'O/N', RateSources.BOE, 'IUDSOIA')
JPY = RateSeries('JPY_POLICY', 'Bank of Japan', 'JPY', 'Policy', RateSources.BIS, 'WS_CBPOL/D.JP')


@pytest.fixture
def downloads(monkeypatch):
    # the answers of the central banks by address, and the requests made
    answers = {
        'data-api.ecb.europa.eu': ECB_CSV,
        'data.snb.ch': SNB_CSV,
        'bankofengland.co.uk': BOE_CSV,
        'stats.bis.org': BIS_CSV,
    }
    requests_made = []

    def fake_get(url, params=None):
        requests_made.append((url, params))
        return next(text for host, text in answers.items() if host in url).lstrip('﻿')

    monkeypatch.setattr(rates, '_get', fake_get)
    return requests_made


def test_central_bank_files_read_as_decimals(downloads):
    estr = rates.download_rates(ESTR)
    assert estr.to_dict() == {pd.Timestamp('2026-11-16'): pytest.approx(0.02441), pd.Timestamp('2026-11-17'): 0.02442}
    # the SNB item named by its dimensions; days without a value left out
    chf = rates.download_rates(CHF_10Y)
    assert chf.to_dict() == {pd.Timestamp('2026-10-29'): pytest.approx(0.0051), pd.Timestamp('2026-10-30'): 0.00505}
    sonia = rates.download_rates(SONIA, start=date(2026, 11, 1))
    assert sonia.index.tolist() == [pd.Timestamp('2026-11-14'), pd.Timestamp('2026-11-17')]
    assert downloads[-1][1]['Datefrom'] == '01/Nov/2026'
    # a central bank policy rate from the BIS
    policy = rates.download_rates(JPY, start=date(2026, 11, 10))
    assert policy.tolist() == [pytest.approx(0.0125)] * 2
    url, params = downloads[-1]
    assert url == 'https://stats.bis.org/api/v1/data/WS_CBPOL/D.JP'
    assert params == {'format': 'csv', 'startPeriod': '2026-11-10'}


def test_snb_table_downloaded_once_per_update(downloads):
    tables = {}
    rates.download_rates(CHF_10Y, snb_tables=tables)
    two_years = CHF_10Y._replace(key='CHF_GOV_2Y', code='rendeiduebd/CHF/2J')
    assert rates.download_rates(two_years, snb_tables=tables).empty  # no value in the file
    assert sum('snb' in url for url, _ in downloads) == 1


def test_update_downloads_the_whole_history_then_the_last_days(temp_db, downloads):
    assert rates.update_rates([ESTR, CHF_10Y]) == {'new': 2}
    assert downloads[0][1].get('startPeriod') is None  # nothing stored: the whole history
    stored = sql.query_interest_rates()
    assert stored['EUR_ESTR'].dropna().iloc[-1] == pytest.approx(0.02442)
    assert stored.loc['2026-10-30', 'CHF_GOV_10Y'] == pytest.approx(0.00505)
    # then from 10 days before the last stored one
    assert rates.update_rates([ESTR]) == {'unchanged': 1}
    assert downloads[-1][1]['startPeriod'] == '2026-11-07'


def test_failed_download_keeps_the_stored_values(temp_db, monkeypatch):
    sql.save_interest_rates('EUR_ESTR', pd.Series([0.02], index=[pd.Timestamp('2026-11-02')]))

    def offline(url, params=None):
        raise ConnectionError('offline')

    monkeypatch.setattr(rates, '_get', offline)
    assert rates.update_rates([ESTR]) == {'failed': 1}
    assert sql.query_interest_rates(['EUR_ESTR'])['EUR_ESTR'].tolist() == [0.02]


def test_rates_updated_once_a_day_from_8(monkeypatch):
    results = []
    monkeypatch.setattr(updates, 'update_rates', lambda: results.pop(0))
    updater = updates.BackgroundUpdater()
    day = datetime(2026, 11, 18)
    assert not updater.update_rates(day.replace(hour=7))  # before 8: not yet
    results.append({'failed': 1})
    assert updater.update_rates(day.replace(hour=8))
    assert not updater.update_rates(day.replace(hour=10))  # a series failed: tried again 3 hours later
    results.append({'updated': 19})
    assert updater.update_rates(day.replace(hour=11))
    assert not updater.update_rates(day.replace(hour=20))  # done for the day
    results.append({'unchanged': 19})
    assert updater.update_rates(day.replace(hour=8) + timedelta(days=1))


def test_starting_lists_saved_in_a_new_database_and_kept_afterwards(temp_db):
    assert sql.query_market_instruments() == DEFAULT_MARKET_INSTRUMENTS
    assert sql.query_taa_instruments() == DEFAULT_TAA_INSTRUMENTS
    assert sql.query_rate_series() == DEFAULT_RATE_SERIES
    # saved lists are never replaced by the starting data
    sql.save_taa_instruments([TaaInstrument('G', 'One', 'ONE')])
    init_db()
    assert sql.query_taa_instruments() == [TaaInstrument('G', 'One', 'ONE')]


def test_paused_instruments_and_series_not_updated(temp_db, downloads):
    sql.save_taa_instruments([TaaInstrument('G', 'One', 'ONE'), TaaInstrument('G', 'Two', 'TWO', active=False)])
    assert [i.ticker for i in sql.query_taa_instruments()] == ['ONE']
    assert [i.ticker for i in sql.query_taa_instruments(active_only=False)] == ['ONE', 'TWO']
    sql.save_rate_series([ESTR, SONIA._replace(active=False)])
    assert rates.update_rates() == {'new': 1}  # only the euro short-term rate
    assert list(sql.query_interest_rates().columns) == ['EUR_ESTR']


def test_research_listings_keep_their_whole_history(temp_db):
    assert history.history_period('XLK') == 'max'
    sql.save_taa_instruments([TaaInstrument('G', 'Paused', 'XLK', active=False)])
    assert history.history_period('XLK') == 'max'  # paused: its stored history is still the whole one
    assert history.history_period('SOME.SW') == history.HISTORY_PERIOD  # the catalog: 10 years


def test_research_listings_updated_after_the_portfolios(temp_db, monkeypatch):
    monkeypatch.setattr(updates, 'list_accounts', lambda: [])
    sql.save_taa_instruments([TaaInstrument('G', 'One', 'ONE'), TaaInstrument('G', 'Two', 'TWO', active=False)])
    assert updates.priority_tickers() == ['ONE']  # the paused one is not updated
