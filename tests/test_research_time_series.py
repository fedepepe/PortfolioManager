import numpy as np
import pandas as pd
import pytest
from test_research_stocks import SP500, stock_data

from portfolio_manager.research import signals
from portfolio_manager.research.backtest import run_backtest, signal_weighted_weights
from portfolio_manager.research.statistics import pooled_regression, time_series_regressions
from portfolio_manager.research.strategies import (
    NO_TRADE_BAND,
    TIME_SERIES_UNIVERSES,
    build_universes,
    run_strategies,
    strategy_positions,
    time_series_strategies,
)

DAYS = pd.bdate_range('2020-01-01', periods=400)


def trending(drifts: dict[str, float], vol: float = 0.01, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    excess = pd.DataFrame({k: rng.normal(d, vol, len(DAYS)) for k, d in drifts.items()}, index=DAYS)
    return (1 + excess).cumprod(), excess


def test_risk_adjusted_return_compares_calm_and_volatile_instruments():
    index, excess = trending({'CALM': 0.0, 'WILD': 0.0})
    excess['WILD'] *= 4  # no trend, four times the noise
    index = (1 + excess).cumprod()
    score = signals.risk_adjusted_return(index, excess, 63).iloc[-300:].abs().mean()
    raw = (index / index.shift(63) - 1).iloc[-300:].abs().mean()
    assert raw['WILD'] / raw['CALM'] > 2.5  # by raw returns the volatile one dominates
    assert 0.6 < score['WILD'] / score['CALM'] < 1.6  # risk-adjusted, they weigh about the same


def test_crossovers_positive_in_an_uptrend_and_negative_in_a_downtrend():
    index, excess = trending({'UP': 0.002, 'DOWN': -0.002}, vol=0.005)
    for score in [signals.ewmac(index, excess, 16, 64), signals.sma_crossover_gap(index, excess, 50, 200)]:
        assert score['UP'].iloc[-1] > 0 > score['DOWN'].iloc[-1]
    assert signals.bollinger_reversal(index, 20)['DOWN'].iloc[-1] > 0  # below its average: a buy for reversal


def test_forecasts_scaled_without_future_data_and_capped():
    rng = np.random.default_rng(1)
    score = pd.DataFrame(rng.normal(0, 3, (300, 4)), index=DAYS[:300])
    scaled = signals.scale_forecast(score)
    assert scaled.abs().max().max() <= signals.FORECAST_CAP
    assert scaled.iloc[-100:].abs().mean().mean() == pytest.approx(0.75, abs=0.25)  # about 1 before the cap
    changed = score.copy()
    changed.iloc[200:] *= 10  # the future changes: the past forecasts do not
    pd.testing.assert_frame_equal(signals.scale_forecast(changed).iloc[:200], scaled.iloc[:200])


def test_positions_proportional_to_the_forecast():
    forecast = pd.DataFrame({'A': [3.0], 'B': [1.0], 'C': [-1.0]})
    budget = pd.DataFrame({'A': [0.5], 'B': [0.25], 'C': [0.25]})
    assert signal_weighted_weights(forecast, budget, 2.0).iloc[0].tolist() == [0.5, 0.125, 0.0]
    assert signal_weighted_weights(forecast, budget, 2.0, long_short=True).iloc[0].tolist() == [0.5, 0.125, -0.125]


def test_no_trade_band_skips_small_changes():
    excess = pd.DataFrame({'A': 0.0, 'B': 0.0}, index=DAYS[:5])
    weights = pd.DataFrame({'A': [0.5, 0.52, 0.8], 'B': [0.5, 0.48, 0.2]}, index=DAYS[:3])
    bands = pd.DataFrame(0.05, index=weights.index, columns=weights.columns)
    result = run_backtest(weights, excess, cost_rate=0.0, bands=bands)
    assert result.turnover.tolist() == pytest.approx([1.0, 0.0, 0.6])  # the 2-point change is within the band
    assert run_backtest(weights, excess, cost_rate=0.0).turnover.iloc[1] == pytest.approx(0.04)


def test_pooled_regression_as_in_the_time_series_regressions():
    rng = np.random.default_rng(3)
    months = pd.date_range('2000-01-31', periods=120, freq='M')
    signal = pd.DataFrame(rng.normal(size=(120, 3)), index=months, columns=list('ABC'))
    forward = 0.2 * signal + pd.DataFrame(rng.normal(size=(120, 3)), index=months, columns=list('ABC'))
    fast = pooled_regression(signal, forward)
    full = time_series_regressions(signal, forward)[1]
    assert fast.t_stat == pytest.approx(full.t_stat) and fast.n_obs == full.n_obs


def test_single_instrument_strategies_on_stocks(temp_db):
    data = stock_data()
    strategies = [s for s in time_series_strategies() if s.universe == SP500]
    assert SP500 in TIME_SERIES_UNIVERSES and len(strategies) == 30
    daily = [s for s in strategies if s.freq == 'B']
    assert daily and all(s.no_trade_band == NO_TRADE_BAND for s in daily)
    assert any('signal-weighted, daily' in s.label for s in daily)
    universe = build_universes(data)[SP500]
    weights, bands = strategy_positions(daily[-1], universe)
    assert (weights.sum(axis=1) <= 1 + 1e-9).all() and (bands.to_numpy() >= 0).all()
    table = run_strategies(strategies, data).table()
    assert table['sharpe'].notna().all() and (table['excess_return_high_costs'] <= table['excess_return']).all()
