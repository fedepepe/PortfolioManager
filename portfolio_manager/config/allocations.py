"""Strategic asset allocations of an account (saved in the database under a name): the target weight of each asset
class, optionally split within a class by region or by sub-class, the instruments chosen for each part, and how
their weights are set (by hand, or by an optimization within ranges around the class targets).
"""

from dataclasses import dataclass, field

from portfolio_manager.optimization.settings import OptimizationSettings, class_bounds

REBALANCING_FREQ_LABELS = {'M': 'Monthly', 'Q': 'Quarterly', 'Y': 'Yearly'}
WEIGHT_MODES = ('manual', 'optimize')
# how a class may be split: by the region of its funds, or by their sub-class (equity segment, bond type, commodity or
# crypto group); see analytics.subclasses
SPLITS = ('region', 'sub_class')
TOLERANCE = 1e-6  # of the sums of weights


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

    def validate(self):
        """Raise ValueError if the allocation cannot be backtested."""
        if not self.name.strip():
            raise ValueError('The allocation needs a name')
        if any(w < 0 for w in self.class_targets.values()) or abs(sum(self.class_targets.values()) - 1) > TOLERANCE:
            raise ValueError('The asset class weights must add up to 100%')
        for asset_class, split in self.splits.items():
            if split not in SPLITS:
                raise ValueError(f'{asset_class}: unknown split {split}')
            weights = [b.weight for b in self.buckets if b.asset_class == asset_class]
            if any(w < 0 for w in weights) or abs(sum(weights) - 1) > TOLERANCE:
                raise ValueError(f'{asset_class}: the weights of its parts must add up to 100%')
        tickers = [i.ticker for i in self.instruments]
        if len(set(tickers)) < len(tickers):
            raise ValueError('An instrument is chosen twice')
        for asset_class in self.active_classes():
            for bucket in self.class_buckets(asset_class):
                chosen = self.bucket_instruments(asset_class, bucket.value)
                label = asset_class if bucket.value is None else f'{asset_class} - {bucket.value}'
                if not chosen:
                    raise ValueError(f'{label}: no instrument chosen')
                if self.weight_mode == 'manual' and abs(sum(i.weight for i in chosen) - 1) > TOLERANCE:
                    raise ValueError(f'{label}: the weights of its instruments must add up to 100%')
        if self.weight_mode not in WEIGHT_MODES:
            raise ValueError(f'Unknown weight mode {self.weight_mode}')
        if self.rebalancing_freq not in REBALANCING_FREQ_LABELS:
            raise ValueError(f'Unknown rebalancing frequency {self.rebalancing_freq}')
        problem = self.settings.validate()
        if problem is not None:
            raise ValueError(problem)

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
