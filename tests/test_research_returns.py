import numpy as np
import pandas as pd
import pytest

import portfolio_manager.storage.queries as sql
from portfolio_manager.config.taa import TaaInstrument
from portfolio_manager.research.returns import ResearchData, business_days, deposit_growth, load_research_data


def test_prices_carried_over_holidays_only_within_their_history():
    days = pd.bdate_range('2024-01-01', periods=6)
    prices = pd.DataFrame({'A': [1.0, np.nan, 1.2, 1.3, np.nan, np.nan], 'B': [np.nan, 2.0, 2.0, np.nan, 2.2, 2.4]})
    prices.index = days
    filled = business_days(prices.drop(days[1]).reindex(days))  # a missing day too
    assert filled['A'].tolist()[:4] == [1.0, 1.0, 1.2, 1.3]
    assert filled['A'].iloc[4:].isna().all()  # after its last price: not carried
    assert np.isnan(filled['B'].iloc[0]) and filled['B'].iloc[3] == 2.0


def test_deposit_earns_the_previous_rate_over_calendar_days():
    days = pd.DatetimeIndex(['2024-01-04', '2024-01-05', '2024-01-08'])  # Thursday, Friday, Monday
    growth = deposit_growth(pd.Series([np.nan, 0.036, 0.072], index=days), 360)
    assert np.isnan(growth.iloc[0])  # no rate yet
    assert growth.iloc[1] == 1.0  # the first day with a rate
    assert growth.iloc[2] == pytest.approx(1.0 + 0.036 * 3 / 360)  # Friday's rate over the weekend


def test_excess_returns_subtract_the_cash_of_the_currency():
    days = pd.bdate_range('2024-01-01', '2024-03-29')
    prices = pd.DataFrame({'EQ': np.linspace(100.0, 110.0, len(days)), 'JP': 50.0}, index=days)
    instruments = pd.DataFrame({'currency': ['USD', 'JPY']}, index=['EQ', 'JP'])
    rates = pd.DataFrame({'USD': 0.05}, index=days)  # no yen rate
    data = ResearchData(prices=prices, instruments=instruments, cash_rates=rates)
    monthly, cash, excess = data.returns('M'), data.cash_returns('M'), data.excess_returns('M')
    assert monthly.index[-1] == pd.Timestamp('2024-03-31')
    # February: 5% on actual/360, compounded every business day (3 days over each weekend)
    steps = np.diff(pd.bdate_range('2024-01-31', '2024-02-29')).astype('timedelta64[D]').astype(int)
    assert cash.loc['2024-02-29', 'USD'] == pytest.approx(np.prod(1 + 0.05 * steps / 360) - 1)
    assert excess.loc['2024-02-29', 'EQ'] == pytest.approx(
        monthly.loc['2024-02-29', 'EQ'] - cash.loc['2024-02-29', 'USD']
    )
    assert excess['JP'].isna().all()  # no cash rate for the yen: unknown


def test_currency_pair_earns_the_interest_of_its_base_currency(temp_db):
    days = pd.bdate_range('2024-01-01', '2024-06-28')
    sql.save_taa_instruments([TaaInstrument('Currencies', 'AUD/USD', 'AUDUSD=X')])
    instrument_id = sql.upsert_yahoo_instrument('AUDUSD=X', {'currency': 'USD'})
    sql.save_yahoo_prices(instrument_id, pd.DataFrame({'close': 0.65}, index=days))  # a flat exchange rate
    sql.save_interest_rates('AUD_POLICY', pd.Series(0.04, index=days))
    sql.save_interest_rates('USD_TBILL_3M', pd.Series(0.05, index=days))
    data = load_research_data()
    assert data.instruments.loc['AUDUSD=X', 'currency'] == 'USD'  # valued in the quote currency
    total, excess = data.returns('M').iloc[1:], data.excess_returns('M').iloc[1:]
    # the deposit in Australian dollars earns 4% (actual/365); minus 5% of US cash (actual/360): negative carry
    assert total['AUDUSD=X'].mean() * 12 == pytest.approx(0.04, abs=0.002)
    assert excess['AUDUSD=X'].mean() * 12 == pytest.approx(0.04 - 0.05 * 365 / 360, abs=0.002)
