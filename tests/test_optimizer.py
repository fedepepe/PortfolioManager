import numpy as np
import pandas as pd
import pytest

import portfolio_manager.optimization.optimizer as po
from portfolio_manager.optimization.optimizer import apply_min_position_size, compute_weights_optim_portfolio
from portfolio_manager.optimization.settings import AllocationStrats, OptimizationSettings

START = pd.Timestamp('2021-07-01')  # optimize the last 6 months only, to keep the tests fast
LATE_START = pd.Timestamp('2021-11-01')  # last 2 months, for the slower solvers (min variance)


def optimize(prices, settings: OptimizationSettings, start=START):
    return compute_weights_optim_portfolio(
        allocation_method=settings.method,
        prices=prices,
        sampling_freq='B',
        optimization_freq=settings.optimization_freq,
        extra_args=settings.extra_args(),
        start_date=start,
    )


def test_only_dates_from_start_are_optimized(prices):
    result = optimize(prices, OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT))
    assert result.weights.index.min() >= START
    assert len(result.weights) == 6


def test_equal_weight_splits_max_invested(prices):
    settings = OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT, max_pf_exposure=0.8)
    weights = optimize(prices, settings).weights
    np.testing.assert_allclose(weights.to_numpy(), 0.2)


def test_equal_weight_capped_at_max_weight_per_asset(prices):
    settings = OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT, max_asset_exposure=0.2)
    weights = optimize(prices, settings).weights
    np.testing.assert_allclose(weights.to_numpy(), 0.2)  # 4 x 20% = 80% invested, the rest in cash


def test_equal_weight_ignores_min_position_and_max_assets(prices):
    settings = OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT, min_position_size=0.5, max_asset_num=1)
    weights = optimize(prices, settings).weights
    np.testing.assert_allclose(weights.to_numpy(), 0.25)


def test_assets_with_short_history_are_left_out(prices_late_asset):
    settings = OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT)
    weights = optimize(prices_late_asset, settings).weights
    # A3 starts in July 2021: less than 3 months of returns (65) until the end of August
    assert weights.loc[:'2021-08-31', 'A3'].isna().all()
    np.testing.assert_allclose(weights.loc[:'2021-08-31', ['A0', 'A1', 'A2']].to_numpy(), 0.3)  # capped at 30%
    np.testing.assert_allclose(weights.loc['2021-09-30':].to_numpy(), 0.25)


@pytest.mark.parametrize('method', [AllocationStrats.MAX_SHARPE, AllocationStrats.MIN_VAR])
def test_optimized_weights_respect_the_settings(prices, method):
    settings = OptimizationSettings(method=method, max_asset_exposure=0.4, min_pf_exposure=0.9, max_pf_exposure=1.0)
    result = optimize(prices, settings, start=LATE_START)
    weights = result.weights
    assert result.failed_dates == []
    assert (weights.to_numpy() >= -1e-6).all() and (weights.to_numpy() <= 0.4 + 1e-6).all()
    invested = weights.sum(axis=1)
    assert ((invested >= 0.9 - 1e-6) & (invested <= 1.0 + 1e-6)).all()


def test_min_invested_capped_at_99_percent(prices):
    settings = OptimizationSettings(method=AllocationStrats.MIN_VAR, min_pf_exposure=1.0, max_pf_exposure=1.0)
    result = optimize(prices, settings, start=LATE_START)
    assert result.failed_dates == []
    # min variance invests as little as allowed: the capped minimum
    np.testing.assert_allclose(result.weights.sum(axis=1), po.MAX_MIN_PF_EXPOSURE, atol=1e-3)  # solver tolerance


def test_min_invested_capped_by_available_assets(prices_late_asset):
    settings = OptimizationSettings(method=AllocationStrats.MIN_VAR, max_asset_exposure=0.3, min_pf_exposure=0.95)
    weights = optimize(prices_late_asset.loc[:'2021-08-31'], settings).weights
    # until A3 has enough history only 3 assets are available: at most 3 x 30% = 90% invested
    assert len(weights) == 2
    np.testing.assert_allclose(weights.sum(axis=1), 0.9, atol=1e-3)


def test_failed_date_keeps_previous_weights(prices, monkeypatch, caplog):
    calls = {'n': 0}
    original = po.PortfolioOptimizer.compute_equal_weights

    def fail_third_date(self):
        calls['n'] += 1
        if calls['n'] == 3:
            return pd.Series(np.nan, index=self.returns_clean.columns, name='weights')
        return original(self)

    monkeypatch.setattr(po.PortfolioOptimizer, 'compute_equal_weights', fail_third_date)
    result = optimize(prices, OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT))
    assert result.failed_dates == [result.weights.index[2]]
    assert result.weights.iloc[2].equals(result.weights.iloc[1])
    assert any(r.levelname == 'WARNING' and 'previous weights kept' in r.getMessage() for r in caplog.records)


def test_min_position_size_drops_and_redistributes():
    weights = pd.Series([0.40, 0.30, 0.20, 0.05, 0.05], index=list('ABCDE'))
    result = apply_min_position_size(weights, min_size=0.1, max_weight=0.45)
    assert (result[['D', 'E']] == 0).all()
    assert result.sum() == pytest.approx(1.0)
    assert result.max() <= 0.45 + 1e-12


def test_min_position_size_leaves_cash_when_capped():
    weights = pd.Series([0.3, 0.3, 0.3, 0.1], index=list('ABCD'))
    result = apply_min_position_size(weights, min_size=0.2, max_weight=0.3)
    np.testing.assert_allclose(result.to_numpy(), [0.3, 0.3, 0.3, 0.0])  # 10% cannot be placed: cash


def test_min_position_size_keeps_failed_weights():
    weights = pd.Series(np.nan, index=list('AB'))
    assert apply_min_position_size(weights, min_size=0.1, max_weight=0.5).isna().all()
