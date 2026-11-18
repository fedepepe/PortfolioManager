"""Strategic asset allocations of an account (saved in the database under a name): the target weight of each asset
class, optionally split within a class by region or by sub-class, the instruments chosen for each part, and how
their weights are set (by hand, or by an optimization within ranges around the class targets).
"""

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from portfolio_manager.optimization.settings import OptimizationSettings, class_bounds

REBALANCING_FREQ_LABELS = {'M': 'Monthly', 'Q': 'Quarterly', 'Y': 'Yearly'}
WEIGHT_MODES = ('manual', 'optimize')
# how a class may be split: by the region of its funds, or by their sub-class (equity segment, bond type, commodity or
# crypto group); see analytics.subclasses
SPLITS = ('region', 'sub_class')
TOLERANCE = 1e-6  # of the sums of weights
NAME_FORBIDDEN = '/\\:*?"<>|'  # characters a name may not contain (it names the files of its backtest)
# the macro asset classes, in their order (the classes of analytics.classification)
ASSET_CLASSES = [
    'Equity',
    'Fixed Income',
    'Money Market',
    'Commodities',
    'Real Estate',
    'Crypto',
    'Multi-Asset',
    'Alternatives',
]
# starting points for the class weights
PRESETS = {
    'Conservative': {'Equity': 0.3, 'Fixed Income': 0.6, 'Money Market': 0.05, 'Commodities': 0.05},
    'Balanced': {'Equity': 0.6, 'Fixed Income': 0.35, 'Commodities': 0.05},
    'Growth': {'Equity': 0.8, 'Fixed Income': 0.15, 'Commodities': 0.05},
    'All equity': {'Equity': 1.0},
}


def percent_text(fraction: float) -> str:
    """A weight as a percentage with the decimals it has (up to 2), e.g. 30%, 30.5%, 24.25%."""
    text = f'{100 * fraction:.2f}'.rstrip('0').rstrip('.')
    return f'{"0" if text == "-0" else text}%'


def default_settings() -> OptimizationSettings:
    """Optimization settings of a new allocation: fully invested, no limit per instrument or on the volatility (the
    asset class ranges keep the allocation).
    """
    return OptimizationSettings(min_pf_exposure=1.0, max_pf_exposure=1.0, max_asset_exposure=1.0, max_vol=None)


@dataclass(frozen=True)
class Bucket:
    """A part of an asset class (a region or a sub-class, by the split of the class) and its weight in the class; the
    part of a class that is not split has no value.
    """

    asset_class: str
    value: str | None
    weight: float  # fraction of the class


@dataclass(frozen=True)
class AllocationInstrument:
    """An instrument chosen for a bucket: its Yahoo Finance listing, the ISIN of its fund, and its weight in the bucket
    (manual weights).
    """

    ticker: str
    isin: str | None
    asset_class: str
    bucket: str | None  # value of the bucket (None: the class is not split)
    weight: float  # fraction of the bucket


@dataclass
class Allocation:
    """A strategic asset allocation: class targets, splits, buckets, instruments, weight mode and settings."""

    name: str
    class_targets: dict[str, float] = field(default_factory=dict)  # fraction of the portfolio
    splits: dict[str, str] = field(default_factory=dict)  # class -> region or sub_class (classes not split: absent)
    buckets: list[Bucket] = field(default_factory=list)
    instruments: list[AllocationInstrument] = field(default_factory=list)
    weight_mode: str = 'manual'
    settings: OptimizationSettings = field(default_factory=default_settings)
    rebalancing_freq: str = 'Q'

    def active_classes(self) -> list[str]:
        """The classes with a positive target, in their order."""
        return [c for c, w in self.class_targets.items() if w > 0]

    def class_buckets(self, asset_class: str) -> list[Bucket]:
        """The buckets of a class: its parts with a positive weight, or the whole class if it is not split."""
        if asset_class not in self.splits:
            return [Bucket(asset_class, None, 1.0)]
        return [b for b in self.buckets if b.asset_class == asset_class and b.weight > 0]

    def bucket_instruments(self, asset_class: str, bucket: str | None) -> list[AllocationInstrument]:
        """The instruments chosen for a bucket."""
        return [i for i in self.instruments if i.asset_class == asset_class and i.bucket == bucket]

    def problems(self) -> list[str]:
        """What keeps the allocation from being backtested (empty if nothing does), in the order of the steps."""
        found = []
        if not self.name.strip():
            found.append('The allocation needs a name')
        elif any(c in self.name for c in NAME_FORBIDDEN):
            found.append(f'The name must not contain any of {" ".join(NAME_FORBIDDEN)}')
        if any(w < 0 for w in self.class_targets.values()) or abs(sum(self.class_targets.values()) - 1) > TOLERANCE:
            total = percent_text(sum(self.class_targets.values()))
            found.append(f'The asset class weights add up to {total}, not 100%')
        for asset_class, split in self.splits.items():
            if split not in SPLITS:
                found.append(f'{asset_class}: unknown split {split}')
                continue
            weights = [b.weight for b in self.buckets if b.asset_class == asset_class]
            if self.class_targets.get(asset_class, 0) > 0 and (
                any(w < 0 for w in weights) or abs(sum(weights) - 1) > TOLERANCE
            ):
                found.append(
                    f'{asset_class}: the weights of its sub-classes add up to {percent_text(sum(weights))}, not 100%'
                )
        tickers = [i.ticker for i in self.instruments]
        if len(set(tickers)) < len(tickers):
            found.append('An instrument is chosen twice')
        for asset_class in self.active_classes():
            for bucket in self.class_buckets(asset_class):
                chosen = self.bucket_instruments(asset_class, bucket.value)
                label = asset_class if bucket.value is None else f'{asset_class} - {bucket.value}'
                if not chosen:
                    found.append(f'{label}: no instrument chosen')
                elif self.weight_mode == 'manual' and abs(sum(i.weight for i in chosen) - 1) > TOLERANCE:
                    found.append(f'{label}: the weights of its instruments must add up to 100%')
        if self.weight_mode not in WEIGHT_MODES:
            found.append(f'Unknown weight mode {self.weight_mode}')
        if self.rebalancing_freq not in REBALANCING_FREQ_LABELS:
            found.append(f'Unknown rebalancing frequency {self.rebalancing_freq}')
        problem = self.settings.validate()
        if problem is not None:
            found.append(problem)
        return found

    def validate(self):
        """Raise ValueError (with the first problem) if the allocation cannot be backtested."""
        found = self.problems()
        if found:
            raise ValueError(found[0])

    def to_dict(self) -> dict[str, Any]:
        """The allocation as plain data (JSON), e.g. to keep it in the browser while it is edited."""
        data = asdict(self)
        data['settings'] = {k: v for k, v in self.settings.to_series().items()}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Allocation':
        """An allocation from its plain data (see to_dict)."""
        return cls(
            name=data['name'],
            class_targets=dict(data['class_targets']),
            splits=dict(data['splits']),
            buckets=[Bucket(**b) for b in data['buckets']],
            instruments=[AllocationInstrument(**i) for i in data['instruments']],
            weight_mode=data['weight_mode'],
            settings=OptimizationSettings.from_series(pd.Series(data['settings'])),
            rebalancing_freq=data['rebalancing_freq'],
        )

    def target_weights(self) -> dict[str, float]:
        """Weight of each instrument in the portfolio with manual weights: class target x weight of its bucket in the
        class x its weight in the bucket (instruments of classes or buckets without weight: 0).
        """
        weights = {i.ticker: 0.0 for i in self.instruments}
        for asset_class in self.active_classes():
            for bucket in self.class_buckets(asset_class):
                for instrument in self.bucket_instruments(asset_class, bucket.value):
                    weights[instrument.ticker] = self.class_targets[asset_class] * bucket.weight * instrument.weight
        return weights

    def asset_classes(self) -> dict[str, str]:
        """The asset class of each instrument (for the asset class constraints of an optimization)."""
        return {i.ticker: i.asset_class for i in self.instruments}

    def class_bounds(self) -> dict[str, tuple[float, float]]:
        """The (min, max) weight of each class with a target: its target plus or minus the tolerance."""
        targets = {c: self.class_targets[c] for c in self.active_classes()}
        return class_bounds(targets, self.settings.class_tolerance)
