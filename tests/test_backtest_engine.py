import numpy as np
import pandas as pd
import pytest

from portfolio.portfolio_backtest_engine import align_df_to_index, backtest_portfolio


def test_align_df_to_index_moves_dates_to_last_available_date():
    index = pd.DatetimeIndex(['2021-01-28', '2021-01-29', '2021-02-01', '2021-02-26'])
    df = pd.DataFrame({'w': [1.0, 2.0, 3.0]}, index=pd.DatetimeIndex(['2021-01-01', '2021-01-31', '2021-02-28']))
    aligned = align_df_to_index(df, index)
    # 2021-01-01 is before the index (dropped); month-ends on weekends move to the previous trading day
    assert aligned.index.tolist() == [pd.Timestamp('2021-01-29'), pd.Timestamp('2021-02-26')]
    assert aligned['w'].tolist() == [2.0, 3.0]


def test_constant_prices_keep_nav_less_costs(prices):
    flat = pd.DataFrame(100.0, index=prices.index[:60], columns=['A', 'B'])
    data = backtest_portfolio(name='flat', prices_df=flat, target_exp=[0.5, 0.5], freq_rebalancing='M')
    # bought once (10 bp on 99.9% of 1e6), never traded again because nothing moves
    assert data.transaction_value.abs().sum(axis=1).gt(0).sum() == 1
    assert data.nav.iloc[-1] == pytest.approx(1e6 - 1e-3 * 0.999 * 1e6, rel=1e-6)
    assert (data.effective_weights['Cash'] >= 0).all()


def test_target_weights_follow_monthly_rebalancing(prices):
    weights = pd.DataFrame(0.25, index=prices.resample('M').last().index, columns=prices.columns)
    data = backtest_portfolio(name='equal', prices_df=prices, target_exp=weights)
    trade_days = data.transaction_value.index[data.transaction_value.abs().sum(axis=1) > 0]
    # trades only on rebalancing dates (last trading day of a month)
    assert trade_days.isin(prices.index.to_series().resample('M').last()).all()
    held = data.effective_weights.drop(columns='Cash').loc[trade_days]
    assert (held.sub(0.25).abs() <= 0.0101).all().all()
    assert (data.effective_weights['Cash'] >= 0).all()
    assert data.nav_eff.iloc[0] == pytest.approx(100.0)


def test_nan_target_weights_keep_units_in_minimal_rebalance(prices):
    # NaN targets are never "beyond tolerance", so a minimal rebalance keeps the units held
    idx = prices.resample('M').last().index[:3]
    weights = pd.DataFrame(0.24, index=idx, columns=prices.columns)
    weights.iloc[2] = np.nan
    data = backtest_portfolio(name='nan', prices_df=prices.loc[: idx[2] + pd.Timedelta(days=5)], target_exp=weights)
    units = data.units.drop(columns='Cash')
    assert (units.iloc[-1] > 0).all()
    assert units.iloc[-1].equals(units.loc[: idx[1]].iloc[-1])
