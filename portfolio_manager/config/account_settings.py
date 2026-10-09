"""Settings of an account chosen by the user (saved in the database), starting with its benchmark."""

from dataclasses import dataclass, field

BENCHMARK_FREQ_LABELS = {'M': 'Monthly', 'Q': 'Quarterly', 'Y': 'Yearly'}


@dataclass(frozen=True)
class BenchmarkComponent:
    """One instrument of a benchmark: label, ISIN or Yahoo Finance ticker searched for its prices, weight."""

    label: str
    search: str
    weight: float


@dataclass
class Benchmark:
    """Instruments of a benchmark with their weights (fractions), rebalanced to the weights at every period."""

    components: list[BenchmarkComponent] = field(default_factory=list)
    rebalancing_freq: str = 'M'

    def validate(self):
        """Raise ValueError if the benchmark cannot be backtested."""
        if not self.components:
            raise ValueError('The benchmark has no instruments')
        labels = [c.label.strip() for c in self.components]
        if any(not label for label in labels) or len(set(labels)) < len(labels):
            raise ValueError('Every instrument of the benchmark needs a different label')
        if any(not c.search.strip() for c in self.components):
            raise ValueError('Every instrument of the benchmark needs an ISIN or a ticker')
        if any(not c.weight > 0 for c in self.components):
            raise ValueError('The weights of the benchmark must be positive')
        if abs(sum(c.weight for c in self.components) - 1.0) > 1e-6:
            raise ValueError('The weights of the benchmark must add up to 100%')
        if self.rebalancing_freq not in BENCHMARK_FREQ_LABELS:
            raise ValueError(f'Unknown rebalancing frequency of the benchmark: {self.rebalancing_freq}')

    def describe(self) -> str:
        """Short description, e.g. "60% IWDC, 20% HYLD, 20% STHC, rebalanced monthly"."""
        weights = ', '.join(f'{c.weight:.0%} {c.label}' for c in self.components)
        return (
            f'{weights}, rebalanced {BENCHMARK_FREQ_LABELS.get(self.rebalancing_freq, self.rebalancing_freq).lower()}'
        )
