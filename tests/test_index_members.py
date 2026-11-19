from datetime import date

import pandas as pd

import portfolio_manager.analytics.price_updates as updates
import portfolio_manager.market_data.history as history
import portfolio_manager.market_data.index_members as index_members
import portfolio_manager.storage.queries as sql

SP500_PAGE = """
<table class="infobox"><tr><th>Not a member table</th></tr></table>
<table class="wikitable sortable" id="constituents">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>Date added</th></tr>
<tr><td><a>MMM</a></td><td>3M</td><td>Industrials</td><td>1957-03-04</td></tr>
<tr><td><a>BRK.B</a></td><td>Berkshire Hathaway</td><td>Financials</td><td>2010-02-16</td></tr>
</table>
<table class="wikitable"><tr><th>Date</th><th>Added</th></tr><tr><td>2026</td><td>XYZ</td></tr></table>
"""
SMI_PAGE = """
<table class="wikitable"><tr><th>Rank</th><th>Name</th><th>Sector</th><th>Ticker</th></tr>
<tr><td>1</td><td>Novartis</td><td>Health care</td><td>NOVN</td></tr></table>
"""


def test_members_read_from_the_wikipedia_table_with_yahoo_tickers():
    sp500, smi = index_members.STOCK_INDICES[0], index_members.STOCK_INDICES[1]
    members = index_members.members_table(sp500, SP500_PAGE)
    assert members.to_dict('records') == [
        {'ticker': 'MMM', 'name': '3M', 'sector': 'Industrials'},
        {'ticker': 'BRK-B', 'name': 'Berkshire Hathaway', 'sector': 'Financials'},  # Yahoo's class B ticker
    ]
    assert index_members.members_table(smi, SMI_PAGE)['ticker'].tolist() == ['NOVN.SW']


def test_members_leaving_the_index_kept_as_former_members(temp_db):
    first = pd.DataFrame({'ticker': ['AAA', 'BBB'], 'name': ['A', 'B'], 'sector': ['Tech', 'Energy']})
    sql.save_index_members('S&P 500', first, date(2026, 1, 5))
    second = pd.DataFrame({'ticker': ['AAA', 'CCC'], 'name': ['A new name', 'C'], 'sector': ['Tech', 'Utilities']})
    sql.save_index_members('S&P 500', second, date(2026, 4, 6))
    current = sql.query_index_members('S&P 500')
    assert current['ticker'].tolist() == ['AAA', 'CCC']
    aaa = current.iloc[0]
    assert (
        aaa['name'] == 'A new name' and aaa['first_seen'] == date(2026, 1, 5) and aaa['last_seen'] == date(2026, 4, 6)
    )
    everyone = sql.query_index_members('S&P 500', current_only=False).set_index('ticker')
    assert not everyone.loc['BBB', 'current'] and everyone.loc['BBB', 'last_seen'] == date(2026, 1, 5)


def test_failed_download_keeps_the_stored_list(temp_db, monkeypatch):
    sql.save_index_members(
        'SMI', pd.DataFrame({'ticker': ['NESN.SW'], 'name': ['Nestle'], 'sector': ['Food']}), date(2026, 1, 5)
    )

    def offline(index):
        raise ConnectionError('offline')

    monkeypatch.setattr(index_members, 'download_members', offline)
    assert index_members.refresh_index_members() == {}
    assert index_members.member_tickers() == ['NESN.SW']


def test_stocks_keep_their_whole_history_and_are_updated(temp_db, monkeypatch):
    members = pd.DataFrame({'ticker': ['AAA', 'BBB'], 'name': ['A', 'B'], 'sector': ['Tech', 'Energy']})
    sql.save_index_members('S&P 500', members, date(2026, 1, 5))
    sql.save_index_members('S&P 500', members.iloc[:1], date(2026, 4, 6))  # BBB leaves the index
    assert history.history_period('AAA') == 'max' and history.history_period('BBB') == 'max'
    sql.save_taa_instruments([])
    monkeypatch.setattr(updates, 'list_accounts', lambda: [])
    assert updates.priority_tickers() == ['AAA']  # today's members only
