"""Allocation methods and the settings of a portfolio optimization."""

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

import pandas as pd


class AllocationStrats(Enum):
    """Allocation methods."""

    MAX_RET = 'max_ret'
    MIN_VAR = 'min_var'
    MAX_SHARPE = 'max_sharpe'
    RISK_PARITY = 'risk_parity'
    EQUAL_WEIGHT = 'equal_weight'


ALLOCATION_STRATS_LABELS = {
    AllocationStrats.MAX_SHARPE: 'Max Sharpe',
    AllocationStrats.MIN_VAR: 'Min variance',
    AllocationStrats.MAX_RET: 'Max return',
    AllocationStrats.RISK_PARITY: 'Risk parity',
    AllocationStrats.EQUAL_WEIGHT: 'Equal weight',
}
# settings that do not apply to equal weights (no ranking of the assets)
EQUAL_WEIGHT_UNUSED_SETTINGS = ('min_position_size', 'max_asset_num')
OPTIMIZATION_FREQ_LABELS = {'M': 'Monthly', 'Q': 'Quarterly', 'Y': 'Yearly'}
OPTIONAL_SETTINGS = ('max_vol', 'target_vol', 'max_asset_num')


def class_bounds(targets: dict[str, float], tolerance: float) -> dict[str, tuple[float, float]]:
    """The (min, max) weight of each asset class: its target plus or minus the tolerance, within 0 and 1."""
    return {c: (max(0.0, t - tolerance), min(1.0, t + tolerance)) for c, t in targets.items()}


# settings of a portfolio optimization (the strategic asset allocations start from their own defaults, see
# config.allocations.default_settings)
@dataclass
class OptimizationSettings:
    """Settings of a portfolio optimization (weights and exposures as fractions)."""

    method: AllocationStrats = AllocationStrats.MAX_SHARPE
    optimization_freq: str = 'M'
    min_asset_exposure: float = 0.0  # min weight of every asset
    max_asset_exposure: float = 0.3  # max weight of every asset
    min_position_size: float = 0.0  # smaller positions are dropped and their weight redistributed (0: off)
    min_pf_exposure: float = 0.9  # min total invested, the rest is cash (with 0%, max Sharpe stayed mostly in cash)
    max_pf_exposure: float = 1.0  # max total invested
    max_vol: float | None = 0.15  # max annual volatility (max Sharpe)
    target_vol: float | None = None  # target annual volatility (max return)
    max_asset_num: int | None = None  # keep only the largest weights
    class_tolerance: float = 0.05  # each asset class may deviate this much from its target (asset allocation)

    def extra_args(self) -> dict[str, Any]:
        """Settings as optimizer arguments (unset and inapplicable ones left out)."""
        args = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in ('method', 'optimization_freq', 'class_tolerance')
        }
        if self.method == AllocationStrats.EQUAL_WEIGHT:
            args = {k: v for k, v in args.items() if k not in EQUAL_WEIGHT_UNUSED_SETTINGS}
        return {k: v for k, v in args.items() if v is not None}

    def to_series(self) -> pd.Series:
        """Settings as a series, to be saved with the results."""
        values = {f.name: getattr(self, f.name) for f in fields(self)}
        values['method'] = self.method.value
        return pd.Series({k: ('' if v is None else v) for k, v in values.items()}, name='value')

    @classmethod
    def from_series(cls, ser: pd.Series) -> 'OptimizationSettings':
        """Missing entries take the default; empty entries mean "not set" for the optional settings."""
        settings = cls()
        for f in fields(cls):
            if f.name not in ser.index:
                continue
            value = ser[f.name]
            if value is None or (isinstance(value, float) and pd.isna(value)) or value == '':
                if f.name in OPTIONAL_SETTINGS:
                    setattr(settings, f.name, None)
                continue
            if f.name == 'method':
                value = AllocationStrats(value)
            elif f.name == 'max_asset_num':
                value = int(value)
            elif f.name != 'optimization_freq':
                value = float(value)
            setattr(settings, f.name, value)
        return settings

    def validate(self) -> str | None:
        """None if the settings can be satisfied, otherwise the reason."""
        if not 0.0 <= self.min_asset_exposure <= self.max_asset_exposure <= 1.0:
            return 'Weight per asset: the minimum must not exceed the maximum (both between 0% and 100%)'
        if not 0.0 <= self.min_pf_exposure <= self.max_pf_exposure <= 1.0:
            return 'Total invested: the minimum must not exceed the maximum (both between 0% and 100%)'
        if self.min_position_size > self.max_asset_exposure:
            return 'Min position size must not exceed the max weight per asset'
        for name, value in [('Max volatility', self.max_vol), ('Target volatility', self.target_vol)]:
            if value is not None and value <= 0.0:
                return f'{name} must be positive'
        if self.max_asset_num is not None and self.max_asset_num < 1:
            return 'Max assets must be at least 1'
        if not 0.0 <= self.class_tolerance <= 1.0:
            return 'Asset class tolerance must be between 0% and 100%'
        return None
