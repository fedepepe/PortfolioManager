import pytest

from portfolio_manager.dashboard.strategies_page import (
    controls_to_settings,
    enable_method_settings,
    settings_to_controls,
)
from portfolio_manager.optimization.settings import AllocationStrats, OptimizationSettings


@pytest.mark.parametrize(
    'settings',
    [
        OptimizationSettings(),
        OptimizationSettings(
            method=AllocationStrats.MIN_VAR,
            optimization_freq='Y',
            min_asset_exposure=0.05,
            max_asset_exposure=0.25,
            min_position_size=0.08,
            min_pf_exposure=0.5,
            max_vol=None,
            max_asset_num=4,
        ),
    ],
)
def test_controls_round_trip(settings):
    assert controls_to_settings(*settings_to_controls(settings)) == settings


def test_controls_are_percentages():
    method, freq, max_assets, weights, min_position, invested, max_vol, target_vol = settings_to_controls(
        OptimizationSettings()
    )
    assert weights == [0.0, 30.0] and invested == [90.0, 100.0] and max_vol == 15.0


@pytest.mark.parametrize(
    'method, disabled',
    [
        (AllocationStrats.MAX_SHARPE, (False, True, False, False)),
        (AllocationStrats.MAX_RET, (True, False, False, False)),
        (AllocationStrats.EQUAL_WEIGHT, (True, True, True, True)),
    ],
)
def test_settings_greyed_out_by_method(method, disabled):
    assert enable_method_settings(method.value) == disabled
