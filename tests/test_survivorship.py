from datetime import date

import numpy as np
import pandas as pd
import pytest
from test_research_stocks import SP500

import portfolio_manager.storage.queries as sql
from portfolio_manager.market_data.index_members import STOCK_INDICES, changes_table, former_member_tickers
from portfolio_manager.research.returns import ResearchData
from portfolio_manager.research.strategies import (
    POINT_IN_TIME_UNIVERSE,
    build_universes,
    run_strategies,
    stock_strategies,
)
from portfolio_manager.research.survivorship import (
    PointInTime,
    equal_weight_returns,
    exit_losses,
    point_in_time,
    point_in_time_members,
    usable_tickers,
)

# the changes table of Wikipedia: two header rows, a date spanning two changes, a change without a removal
CHANGES_PAGE = """
<table class="wikitable" id="changes">
<tr><th rowspan="2">Effective Date</th><th colspan="2">Added</th><th colspan="2">Removed</th>
<th rowspan="2">Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td rowspan="2">March 4, 2024</td><td>NEW</td><td>New Co</td><td>OLD</td><td>Old Co</td><td>Market cap</td></tr>
<tr><td>BRK.B</td><td>Berkshire</td><td>GONE</td><td>Gone Co</td><td>Acquired</td></tr>
<tr><td>June 1, 2023</td><td>ADD</td><td>Add Co</td><td></td><td></td><td>Spin-off</td></tr>
</table>
"""
DAYS = pd.bdate_range('2023-05-01', '2024-06-28')


def test_changes_read_with_dates_spanning_several_rows():
    changes = changes_table(STOCK_INDICES[0], CHANGES_PAGE)
    assert changes['date'].tolist() == [pd.Timestamp('2024-03-04')] * 2 + [pd.Timestamp('2023-06-01')]
    assert changes['added'].tolist() == ['NEW', 'BRK-B', 'ADD'] and changes['removed'].tolist() == ['OLD', 'GONE', '']


def test_members_of_each_day_rebuilt_from_todays_list():
    changes = changes_table(STOCK_INDICES[0], CHANGES_PAGE).replace('', None)
    members = point_in_time_members(['NEW', 'BRK-B', 'ADD', 'KEEP'], changes, DAYS)
    on = lambda day: set(members.columns[members.loc[day]])  # noqa: E731
    assert on('2024-03-04') == {'NEW', 'BRK-B', 'ADD', 'KEEP'}  # members from the effective date
    assert on('2024-03-01') == {'OLD', 'GONE', 'ADD', 'KEEP'}  # removed on 4 March: members the day before
    assert on('2023-05-31') == {'OLD', 'GONE', 'KEEP'}  # before ADD joined


def test_former_member_usable_only_with_prices_of_its_membership():
    members = pd.DataFrame({'A': True, 'B': True, 'C': True}, index=DAYS[:100])
    excess = pd.DataFrame({'A': 0.0, 'B': 0.0, 'C': 0.0}, index=DAYS)
    excess.loc[DAYS[:50], 'B'] = np.nan  # prices for half its membership
    excess['C'] = np.nan
    excess.loc[DAYS[150] :, 'C'] = 0.01  # prices only after its membership: another company's ticker now
    coverage = usable_tickers(members, excess)
    assert coverage.to_dict() == {'A': 1.0, 'B': 0.5, 'C': 0.0}


def test_missing_members_bracketed_by_a_total_loss_on_removal():
    days = DAYS[:6]
    all_members = pd.DataFrame(
        {'A': [True] * 6, 'B': [True] * 6, 'C': [True] * 6, 'GONE': [True, True, True, False, False, False]},
        index=days,
    )
    pit = PointInTime(all_members[['A', 'B', 'C']], all_members, pd.Series(dtype=float))
    losses = exit_losses(pit)
    # GONE (1 of 4 members) leaves after the third day: the day after, the index loses its share
    assert losses.tolist() == pytest.approx([0, 0, 0, -0.25, 0, 0])
    excess = pd.DataFrame({'A': 0.01, 'B': 0.03, 'C': 0.02}, index=days)
    assert equal_weight_returns(excess, pit.members).iloc[1:].tolist() == pytest.approx([0.02] * 5)


def stocks_with_a_former_member() -> ResearchData:
    # S&P 500 stocks with random prices; FORMER left the index (and the prices stop) in mid-2023
    days = pd.bdate_range('2020-01-01', '2024-06-28')
    rng = np.random.default_rng(4)
    tickers = [f'S{n:02d}' for n in range(12)] + ['FORMER']
    prices = pd.DataFrame(
        np.exp(np.cumsum(rng.normal(0.0003, 0.015, (len(days), 13)), axis=0)), index=days, columns=tickers
    )
    prices.loc['2023-07-03':, 'FORMER'] = np.nan
    instruments = pd.DataFrame({'group': SP500, 'currency': 'USD', 'sector': 'Tech'}, index=tickers)
    instruments.loc['FORMER', 'group'] = 'S&P 500 former members'
    return ResearchData(prices=prices, instruments=instruments, cash_rates=pd.DataFrame({'USD': 0.02}, index=days))


def test_point_in_time_universe_holds_stocks_only_while_members(temp_db, monkeypatch):
    import portfolio_manager.research.survivorship as survivorship

    monkeypatch.setattr(survivorship, 'CHANGES_RELIABLE_FROM', pd.Timestamp('2021-01-01'))
    current = pd.DataFrame({'ticker': [f'S{n:02d}' for n in range(12)], 'name': 'x', 'sector': 'Tech'})
    sql.save_index_members('S&P 500', current, date(2024, 6, 28))
    changes = pd.DataFrame(
        {
            'date': pd.to_datetime(['2023-07-03', '2022-01-03']),
            'added': ['S11', 'S10'],
            'added_name': ['', ''],
            'removed': ['FORMER', ''],
            'removed_name': ['', ''],
            'reason': ['', ''],
        }
    )
    sql.save_index_changes('S&P 500', changes)
    assert former_member_tickers('S&P 500', since=pd.Timestamp('2021-01-01')) == ['FORMER']
    data = stocks_with_a_former_member()
    pit = point_in_time(data, start=pd.Timestamp('2021-01-01'))
    assert 'FORMER' in pit.members.columns and pit.missing.empty
    assert pit.members.loc['2023-06-30', 'FORMER'] and not pit.members.loc['2023-06-30', 'S11']
    assert not pit.members.loc['2021-06-30', 'S10']  # joined in 2022
    universes = build_universes(data)
    assert POINT_IN_TIME_UNIVERSE in universes
    strategies = [s for s in stock_strategies() if s.universe == POINT_IN_TIME_UNIVERSE]
    results = run_strategies(strategies[:2], data)
    weights = results.backtests[strategies[0]].weights
    assert (weights.loc[:'2021-12-31', 'S10'] == 0).all()  # not held before joining
    assert (weights.loc['2023-07-31':, 'FORMER'] == 0).all()  # nor after leaving
