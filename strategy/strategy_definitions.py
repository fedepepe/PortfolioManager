from dataclasses import dataclass, fields
from enum import Enum
from typing import Optional, Dict, Any

import pandas as pd

from engines.reporting import compute_portfolio_metrics


class AllocationStrats(Enum):
	MAX_RET = 'max_ret'
	MIN_VAR = 'min_var'
	MAX_SHARPE = 'max_sharpe'
	RISK_PARITY = 'risk_parity'
	EQUAL_WEIGHT = 'equal_weight'


ALLOCATION_STRATS_LABELS = {AllocationStrats.MAX_SHARPE: 'Max Sharpe',
                            AllocationStrats.MIN_VAR: 'Min variance',
                            AllocationStrats.MAX_RET: 'Max return',
                            AllocationStrats.RISK_PARITY: 'Risk parity',
                            AllocationStrats.EQUAL_WEIGHT: 'Equal weight'}
# settings that do not apply to equal weights (no ranking of the assets)
EQUAL_WEIGHT_UNUSED_SETTINGS = ('min_position_size', 'max_asset_num')
OPTIMIZATION_FREQ_LABELS = {'M': 'Monthly', 'Q': 'Quarterly', 'Y': 'Yearly'}
OPTIONAL_SETTINGS = ('max_vol', 'target_vol', 'max_asset_num')


# settings of a portfolio optimization; the defaults are used when an account has no saved optimization
@dataclass
class OptimizationSettings:
	method: AllocationStrats = AllocationStrats.MAX_SHARPE
	optimization_freq: str = 'M'
	min_asset_exposure: float = 0.  # min weight of every asset
	max_asset_exposure: float = 0.3  # max weight of every asset
	min_position_size: float = 0.  # smaller positions are dropped and their weight redistributed (0: off)
	min_pf_exposure: float = 0.9  # min total invested, the rest is cash (with 0%, max Sharpe stayed mostly in cash)
	max_pf_exposure: float = 1.  # max total invested
	max_vol: Optional[float] = 0.15  # max annual volatility (max Sharpe)
	target_vol: Optional[float] = None  # target annual volatility (max return)
	max_asset_num: Optional[int] = None  # keep only the largest weights

	def extra_args(self) -> Dict[str, Any]:
		args = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ('method', 'optimization_freq')}
		if self.method == AllocationStrats.EQUAL_WEIGHT:
			args = {k: v for k, v in args.items() if k not in EQUAL_WEIGHT_UNUSED_SETTINGS}
		return {k: v for k, v in args.items() if v is not None}

	def to_series(self) -> pd.Series:
		values = {f.name: getattr(self, f.name) for f in fields(self)}
		values['method'] = self.method.value
		return pd.Series({k: ('' if v is None else v) for k, v in values.items()}, name='value')

	@classmethod
	def from_series(cls, ser: pd.Series) -> 'OptimizationSettings':
		# missing entries take the default; empty entries mean "not set" for the optional settings
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

	def validate(self, n_assets: Optional[int] = None) -> Optional[str]:
		# None if the settings can be satisfied, otherwise the reason
		if not 0. <= self.min_asset_exposure <= self.max_asset_exposure <= 1.:
			return 'Weight per asset: the minimum must not exceed the maximum (both between 0% and 100%)'
		if not 0. <= self.min_pf_exposure <= self.max_pf_exposure <= 1.:
			return 'Total invested: the minimum must not exceed the maximum (both between 0% and 100%)'
		if self.min_position_size > self.max_asset_exposure:
			return 'Min position size must not exceed the max weight per asset'
		for name, value in [('Max volatility', self.max_vol), ('Target volatility', self.target_vol)]:
			if value is not None and value <= 0.:
				return f'{name} must be positive'
		if self.max_asset_num is not None and self.max_asset_num < 1:
			return 'Max assets must be at least 1'
		if n_assets is not None:
			if self.min_asset_exposure * n_assets > self.max_pf_exposure + 1e-9:
				return (f'Min weight {self.min_asset_exposure:.0%} x {n_assets} assets = '
				        f'{self.min_asset_exposure * n_assets:.0%} exceeds the max total invested '
				        f'{self.max_pf_exposure:.0%}')
			if self.max_asset_exposure * n_assets < self.min_pf_exposure - 1e-9:
				return (f'Max weight {self.max_asset_exposure:.0%} x {n_assets} assets = '
				        f'{self.max_asset_exposure * n_assets:.0%} is below the min total invested '
				        f'{self.min_pf_exposure:.0%}')
		return None

	def describe(self) -> str:
		text = (f'{ALLOCATION_STRATS_LABELS[self.method]}, {OPTIMIZATION_FREQ_LABELS.get(self.optimization_freq, self.optimization_freq).lower()}, '
		        f'weight {self.min_asset_exposure:.0%}-{self.max_asset_exposure:.0%}, '
		        f'invested {self.min_pf_exposure:.0%}-{self.max_pf_exposure:.0%}')
		equal_weight = self.method == AllocationStrats.EQUAL_WEIGHT
		if self.min_position_size > 0 and not equal_weight:
			text += f', min position {self.min_position_size:.0%}'
		if self.method == AllocationStrats.MAX_SHARPE and self.max_vol is not None:
			text += f', max vol {self.max_vol:.0%}'
		if self.method == AllocationStrats.MAX_RET and self.target_vol is not None:
			text += f', target vol {self.target_vol:.0%}'
		if self.max_asset_num is not None and not equal_weight:
			text += f', max {self.max_asset_num} assets'
		return text


# class StrategyData:
# 	def __init__(self, hist_data: HistPortfolioData):
# 		self.hist_data = hist_data
# 		self.perf_dct = compute_portfolio_metrics(nav=hist_data.nav_eff)
