import numpy as np
import pandas as pd
import pytest

import portfolio_manager.optimization.optimizer as po
from portfolio_manager.optimization.optimizer import apply_min_position_size, compute_weights_optim_portfolio
from portfolio_manager.optimization.settings import AllocationStrats, OptimizationSettings, class_bounds

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


def test_fully_invested(prices):
    settings = OptimizationSettings(method=AllocationStrats.MIN_VAR, min_pf_exposure=1.0, max_pf_exposure=1.0)
    result = optimize(prices, settings, start=LATE_START)
    assert result.failed_dates == []
    # min variance invests as little as allowed: all of it (the solver handles min = max = 100%)
    np.testing.assert_allclose(result.weights.sum(axis=1), 1.0, atol=1e-6)


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


# asset classes (strategic asset allocation): A0 and A1 equity, A2 and A3 bonds
CLASSES = {'A0': 'Equity', 'A1': 'Equity', 'A2': 'Fixed Income', 'A3': 'Fixed Income'}


def optimize_classes(prices, settings: OptimizationSettings, targets: dict[str, float], start=START):
    extra_args = settings.extra_args()
    extra_args['asset_classes'] = CLASSES
    extra_args['class_bounds'] = class_bounds(targets, settings.class_tolerance)
    return compute_weights_optim_portfolio(
        allocation_method=settings.method,
        prices=prices,
        sampling_freq='B',
        optimization_freq=settings.optimization_freq,
        extra_args=extra_args,
        start_date=start,
    )


def class_weights(weights: pd.DataFrame) -> pd.DataFrame:
    return weights.T.groupby(CLASSES).sum().T


def test_class_bounds_from_targets_and_tolerance():
    assert class_bounds({'Equity': 0.6, 'Crypto': 0.02}, 0.05) == {
        'Equity': pytest.approx((0.55, 0.65)),
        'Crypto': pytest.approx((0.0, 0.07)),  # not below 0
    }


def test_optimized_class_weights_stay_within_the_tolerance(prices):
    settings = OptimizationSettings(
        method=AllocationStrats.MAX_SHARPE,
        max_asset_exposure=1.0,
        min_pf_exposure=1.0,
        max_vol=None,
        class_tolerance=0.05,
    )
    result = optimize_classes(prices, settings, {'Equity': 0.6, 'Fixed Income': 0.4}, start=LATE_START)
    assert not result.failed_dates
    by_class = class_weights(result.weights)
    assert (by_class['Equity'] >= 0.55 - 1e-4).all() and (by_class['Equity'] <= 0.65 + 1e-4).all()
    assert (by_class['Fixed Income'] >= 0.35 - 1e-4).all() and (by_class['Fixed Income'] <= 0.45 + 1e-4).all()


def test_exact_class_targets_with_zero_tolerance(prices):
    settings = OptimizationSettings(
        method=AllocationStrats.MIN_VAR, max_asset_exposure=1.0, min_pf_exposure=1.0, class_tolerance=0.0
    )
    result = optimize_classes(prices, settings, {'Equity': 0.7, 'Fixed Income': 0.3}, start=LATE_START)
    assert not result.failed_dates
    np.testing.assert_allclose(class_weights(result.weights)['Equity'].to_numpy(), 0.7, atol=1e-3)


def test_class_without_assets_with_history_has_no_bound(prices_late_asset):
    # A3 alone in its class: before it has 3 months of returns, its class bound is dropped (no failed optimization)
    classes = {'A0': 'Equity', 'A1': 'Equity', 'A2': 'Equity', 'A3': 'Crypto'}
    settings = OptimizationSettings(method=AllocationStrats.MAX_SHARPE, max_asset_exposure=1.0, max_vol=None)
    extra_args = settings.extra_args()
    extra_args['asset_classes'] = classes
    extra_args['class_bounds'] = {'Equity': (0.85, 0.95), 'Crypto': (0.05, 0.15)}
    result = compute_weights_optim_portfolio(
        allocation_method=settings.method,
        prices=prices_late_asset,
        sampling_freq='B',
        optimization_freq='M',
        extra_args=extra_args,
        start_date=START,
    )
    assert not result.failed_dates
    assert result.weights.loc[:'2021-08-31', 'A3'].isna().all()
    late = result.weights.loc['2021-09-30':]
    assert (late['A3'] >= 0.05 - 1e-4).all() and (late['A3'] <= 0.15 + 1e-4).all()


def test_class_minimum_lowered_to_what_its_assets_can_hold(prices):
    optimizer = po.PortfolioOptimizer(
        optimization_type=AllocationStrats.MIN_VAR,
        returns=prices.pct_change(),
        bounds_weights=(0.0, 0.3),
        asset_classes={'A0': 'Crypto'},
        class_bounds={'Crypto': (0.5, 0.6), 'Gold': (0.1, 0.2)},  # Gold has no asset
    )
    assert optimizer.class_bounds == {'Crypto': (0.3, 0.6)}  # one asset at most 30%


def test_equal_weight_splits_each_class_target(prices):
    settings = OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT, max_asset_exposure=1.0, class_tolerance=0.05)
    weights = optimize_classes(prices, settings, {'Equity': 0.6, 'Fixed Income': 0.4}).weights
    np.testing.assert_allclose(weights[['A0', 'A1']].to_numpy(), 0.3)
    np.testing.assert_allclose(weights[['A2', 'A3']].to_numpy(), 0.2)


@pytest.mark.parametrize(
    'method',
    [AllocationStrats.MAX_RET, AllocationStrats.MIN_VAR, AllocationStrats.MAX_SHARPE, AllocationStrats.RISK_PARITY],
)
def test_every_method_respects_the_class_ranges(prices, method):
    settings = OptimizationSettings(
        method=method, max_asset_exposure=1.0, min_pf_exposure=1.0, max_vol=None, class_tolerance=0.05
    )
    result = optimize_classes(prices, settings, {'Equity': 0.6, 'Fixed Income': 0.4})
    assert not result.failed_dates
    by_class = class_weights(result.weights)
    assert by_class['Equity'].between(0.55 - 1e-6, 0.65 + 1e-6).all()
    assert by_class['Fixed Income'].between(0.35 - 1e-6, 0.45 + 1e-6).all()
