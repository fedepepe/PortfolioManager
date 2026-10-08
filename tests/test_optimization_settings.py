import pytest

from portfolio_manager.optimization.settings import AllocationStrats, OptimizationSettings


def test_defaults():
    settings = OptimizationSettings()
    assert settings.method == AllocationStrats.MAX_SHARPE
    assert (settings.min_pf_exposure, settings.max_pf_exposure) == (0.9, 1.0)
    assert settings.describe() == 'Max Sharpe, monthly, weight 0%-30%, invested 90%-100%, max vol 15%'


@pytest.mark.parametrize(
    'settings',
    [
        OptimizationSettings(),
        OptimizationSettings(
            method=AllocationStrats.MAX_RET,
            optimization_freq='Q',
            max_vol=None,
            target_vol=0.1,
            max_asset_num=3,
            min_position_size=0.05,
        ),
        OptimizationSettings(method=AllocationStrats.EQUAL_WEIGHT, min_asset_exposure=0.05, max_asset_exposure=0.25),
    ],
)
def test_series_round_trip(settings):
    assert OptimizationSettings.from_series(settings.to_series()) == settings


def test_missing_entries_take_the_defaults():
    ser = OptimizationSettings(method=AllocationStrats.MIN_VAR).to_series().drop(['max_vol', 'min_pf_exposure'])
    settings = OptimizationSettings.from_series(ser)
    assert settings.method == AllocationStrats.MIN_VAR
    assert settings.max_vol == OptimizationSettings().max_vol
    assert settings.min_pf_exposure == OptimizationSettings().min_pf_exposure


def test_extra_args_skip_unset_settings():
    args = OptimizationSettings(max_vol=None).extra_args()
    assert 'max_vol' not in args and 'target_vol' not in args and 'method' not in args


def test_extra_args_of_equal_weight_skip_ranking_settings():
    args = OptimizationSettings(
        method=AllocationStrats.EQUAL_WEIGHT, min_position_size=0.1, max_asset_num=2
    ).extra_args()
    assert 'min_position_size' not in args and 'max_asset_num' not in args


@pytest.mark.parametrize(
    'settings, n_assets, message',
    [
        (OptimizationSettings(min_asset_exposure=0.4, max_asset_exposure=0.3), None, 'Weight per asset'),
        (OptimizationSettings(min_pf_exposure=1.0, max_pf_exposure=0.9), None, 'Total invested'),
        (OptimizationSettings(min_position_size=0.5), None, 'Min position size'),
        (OptimizationSettings(max_vol=0.0), None, 'Max volatility'),
        (OptimizationSettings(max_asset_num=0), None, 'Max assets'),
        (OptimizationSettings(min_asset_exposure=0.3), 4, 'exceeds the max total invested'),
        (OptimizationSettings(max_asset_exposure=0.2), 4, 'is below the min total invested'),
    ],
)
def test_validation_messages(settings, n_assets, message):
    assert message in settings.validate(n_assets=n_assets)


def test_valid_settings():
    assert OptimizationSettings().validate(n_assets=6) is None
