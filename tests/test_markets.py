from datetime import date

import numpy as np
import pandas as pd
import pytest

import portfolio_manager.analytics.markets as markets
from portfolio_manager.config.markets import MARKET_INSTRUMENTS, MarketInstrument
from portfolio_manager.dashboard.markets_page import TOTAL_COLUMN, get_fig_market_returns, scale_limit

TODAY = date(2026, 11, 17)


def closes_by_month(month_ends: dict[str, float]) -> pd.Series:
    # a close on the 10th and on the last business day of each month (the last one counts)
    rows = {}
    for month, close in month_ends.items():
        period = pd.Period(month, freq='M')
        rows[pd.Timestamp(period.start_time) + pd.Timedelta(days=9)] = close * 0.9
        rows[pd.bdate_range(period.start_time, period.end_time)[-1]] = close
    return pd.Series(rows).sort_index()


def test_monthly_returns_of_the_last_12_months_and_their_total():
    # month ends from October 2025 (the base) to November 2026 (the current month, to date: up to the 17th)
    months = pd.period_range('2025-10', '2026-11', freq='M')
    values = 100.0 * 1.01 ** np.arange(len(months))
    series = closes_by_month({str(m): v for m, v in zip(months, values, strict=True)})
    series = series[series.index <= pd.Timestamp(TODAY)]  # nothing after today
    series[pd.Timestamp(TODAY)] = values[-1]  # the last close of the current month, today
    closes = pd.DataFrame({'AAA': series, 'BBB': np.nan})
    returns, totals = markets.monthly_returns(closes, TODAY)
    assert list(returns.columns) == list(pd.period_range('2025-12', '2026-11', freq='M'))  # 12 months, November last
    assert returns.loc['AAA'].tolist() == pytest.approx([0.01] * 12)
    assert totals['AAA'] == pytest.approx(1.01**12 - 1)  # from the end of November 2025 to today
    assert returns.loc['BBB'].isna().all() and np.isnan(totals['BBB'])  # no history


def test_month_without_close_has_no_return_and_the_total_ends_at_the_last_close():
    series = closes_by_month({'2025-11': 100.0, '2025-12': 110.0, '2026-02': 121.0})  # no close in January
    returns, totals = markets.monthly_returns(pd.DataFrame({'AAA': series}), date(2026, 3, 2))
    row = returns.loc['AAA']
    assert row[pd.Period('2025-12', 'M')] == pytest.approx(0.1)
    assert np.isnan(row[pd.Period('2026-01', 'M')]) and np.isnan(row[pd.Period('2026-02', 'M')])
    assert np.isnan(row[pd.Period('2026-03', 'M')])  # the current month has no close yet
    assert np.isnan(totals['AAA'])  # no close a month before the first month (March 2025)
    returns, totals = markets.monthly_returns(pd.DataFrame({'AAA': series}), date(2026, 3, 2), months=4)
    assert totals['AAA'] == pytest.approx(0.21)  # from the end of November 2025 to the last close (February 2026)


def test_market_returns_in_the_order_of_the_instruments(monkeypatch):
    instruments = [MarketInstrument('G', 'One', 'ONE'), MarketInstrument('G', 'Two', 'TWO')]
    monkeypatch.setattr(markets, 'MARKET_INSTRUMENTS', instruments)
    series = closes_by_month({'2026-10': 100.0, '2026-11': 105.0})
    monkeypatch.setattr(markets, 'load_adjusted_closes', lambda tickers: pd.DataFrame({'TWO': series}))
    returns, totals, last_day = markets.market_returns(today=date(2026, 11, 30))
    assert list(returns.index) == ['ONE', 'TWO'] and returns.loc['ONE'].isna().all()
    assert returns.loc['TWO', pd.Period('2026-11', 'M')] == pytest.approx(0.05)
    assert last_day == series.index[-1]


def test_new_market_instrument_gets_its_information(monkeypatch):
    instruments = [MarketInstrument('G', 'Old', 'OLD'), MarketInstrument('G', 'New', 'NEW')]
    monkeypatch.setattr(markets, 'MARKET_INSTRUMENTS', instruments)
    monkeypatch.setattr(markets.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(markets, 'listing_currencies', lambda tickers: {'OLD': 'EUR'})
    monkeypatch.setattr(markets, 'refresh_history', lambda ticker: 'updated' if ticker == 'OLD' else 'new')
    info = []
    monkeypatch.setattr(markets, 'refresh_info', info.append)
    assert markets.refresh_market_data() == {'updated': 1, 'new': 1}
    assert info == ['NEW']


def test_two_tables_with_symmetric_color_scales():
    tickers = [i.ticker for i in MARKET_INSTRUMENTS]
    periods = pd.period_range(end=pd.Period(TODAY, 'M'), periods=12, freq='M')
    returns = pd.DataFrame(0.01, index=tickers, columns=periods)
    returns.iloc[0, 0] = -0.08  # the largest monthly return in magnitude
    totals = pd.Series(0.1, index=tickers)
    totals.iloc[1] = 0.35
    fig = get_fig_market_returns(returns, totals)
    monthly, total = fig.data
    groups = list(dict.fromkeys(i.group for i in MARKET_INSTRUMENTS))
    assert len(monthly.y) == len(tickers) + len(groups)  # a title row per group
    assert monthly.y[0] == f'<b>{groups[0]}</b>' and monthly.y[1] == f'{MARKET_INSTRUMENTS[0].label} ({tickers[0]})'
    assert len(monthly.x) == 12 and monthly.x[-1] == 'Nov 26' and list(total.x) == [TOTAL_COLUMN]
    # each scale: symmetric around 0 (white), up to the largest absolute return of its table
    assert (monthly.zmin, monthly.zmax, monthly.zmid) == (-0.08, 0.08, 0.0)
    assert (total.zmin, total.zmax) == (-0.35, 0.35)
    assert monthly.colorscale[0][1] == 'rgb(215, 25, 28)' and monthly.colorscale[1][1] == 'rgb(255, 255, 255)'
    assert monthly.text[1][0] == '-8.0%' and total.text[2][0] == '+35.0%'
    assert scale_limit(np.array([np.nan])) > 0  # no data: still a valid scale
