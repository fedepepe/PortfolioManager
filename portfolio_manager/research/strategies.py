"""Strategies of the research: universes of instruments, the signals applied to them and their backtests.

A universe is a group of instruments (equity indices, sectors, bonds, commodities), the currencies against the US
dollar, or the currencies an investor holds against their own (currency hedging: a currency is left unhedged while the
strategy holds it, hedged otherwise). Each strategy turns a signal into weights on every signal day (time series: each
instrument on its own; cross section: the best ranked ones), long-only or long/short, and is compared with the
benchmark of its universe: every instrument held in equal shares (long-only), never hedged (currency hedging), or cash
(long/short).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
import pandas as pd

from portfolio_manager.research import signals
from portfolio_manager.research.backtest import (
    BacktestResult,
    budgets,
    cross_section_weights,
    run_backtest,
    signal_days,
    summary,
    time_series_weights,
)
from portfolio_manager.research.currencies import CurrencyIndices, excess_index
from portfolio_manager.research.returns import ResearchData, load_research_data
from portfolio_manager.storage.queries import query_interest_rates

ASSET_GROUPS = ['Equity indices', 'US sectors', 'European sectors', 'Bonds', 'Commodities']
CURRENCIES_UNIVERSE = 'Currencies vs USD'
HEDGING_PREFIX = 'Hedging '  # + the investor's currency
INVESTOR_CURRENCIES = ['CHF', 'EUR']  # of the accounts
HEDGED_CURRENCIES = ['USD', 'EUR', 'GBP', 'CHF']  # the foreign currencies of their holdings
# 10-year government yield of each currency: with the cash rate, the slope of the yield curve (the carry of bonds)
LONG_YIELDS = {'USD': 'USD_GOV_10Y', 'EUR': 'EUR_GOV_10Y', 'CHF': 'CHF_GOV_10Y', 'GBP': 'GBP_GOV_10Y'}


@dataclass
class Universe:
    """Instruments traded together: their daily excess returns, their carry (if any) and the kind of benchmark."""

    name: str
    excess: pd.DataFrame
    carry: pd.DataFrame | None = None
    hedging: bool = False  # currency hedging: the benchmark is never hedged

    @cached_property
    def index(self) -> pd.DataFrame:
        """Excess return index of each instrument."""
        return excess_index(self.excess)

    @cached_property
    def vol(self) -> pd.DataFrame:
        """Recent volatility of each instrument (annualized)."""
        return signals.volatility(self.excess)


@dataclass(frozen=True)
class Strategy:
    """A signal applied to a universe: time series (ts) or cross section (xs), long-only or long/short, on the last
    business day of each period (freq), optionally in equal risk shares.
    """

    family: str
    name: str
    universe: str
    signal: Callable[[Universe], pd.DataFrame] = field(compare=False, repr=False)
    construction: str = 'ts'
    long_short: bool = False
    freq: str = 'M'
    threshold: float = 0.0
    risk_parity: bool = False

    @property
    def label(self) -> str:
        """Name, construction and side, e.g. 'Momentum 12M (TS, long-only)'."""
        side = 'long/short' if self.long_short else 'long-only'
        return f'{self.name} ({self.construction.upper()}, {side})'


def term_spreads(data: ResearchData, instruments: pd.DataFrame) -> pd.DataFrame:
    """Slope of the yield curve of the currency of each instrument: 10-year yield minus the cash rate (decimals)."""
    yields = query_interest_rates(list(LONG_YIELDS.values()))
    yields = yields.reindex(yields.index.union(data.prices.index)).ffill().reindex(data.prices.index)
    spreads = {}
    for ticker, currency in instruments['currency'].items():
        if currency in LONG_YIELDS and LONG_YIELDS[currency] in yields:
            spreads[ticker] = yields[LONG_YIELDS[currency]] - data.cash_rates[currency]
    return pd.DataFrame(spreads, index=data.prices.index)


def build_universes(data: ResearchData) -> dict[str, Universe]:
    """The universes of the research data (see the module)."""
    excess = data.excess_returns('B')
    universes = {}
    for group in ASSET_GROUPS:
        tickers = data.instruments.index[data.instruments['group'] == group]
        carry = term_spreads(data, data.instruments.loc[tickers]) if group == 'Bonds' else None
        universes[group] = Universe(group, excess[tickers], carry)
    currencies = CurrencyIndices(data)
    others = [c for c in currencies.currencies() if c != 'USD']
    universes[CURRENCIES_UNIVERSE] = Universe(
        CURRENCIES_UNIVERSE, currencies.excess_returns(others, 'USD'), currencies.carry(others, 'USD')
    )
    for home in INVESTOR_CURRENCIES:
        foreign = [c for c in HEDGED_CURRENCIES if c != home]
        name = HEDGING_PREFIX + home
        universes[name] = Universe(
            name, currencies.excess_returns(foreign, home), currencies.carry(foreign, home), hedging=True
        )
    return universes


def _carry(universe: Universe) -> pd.DataFrame:
    if universe.carry is None:
        raise ValueError(f'No carry for {universe.name}')
    return universe.carry


def _carry_and_trend(universe: Universe) -> pd.DataFrame:
    # positive only when both the carry and the 12-month trend are
    both = (_carry(universe) > 0) & (signals.momentum(universe.index, 12) > 0)
    return both.astype(float).where(universe.index.notna()) * 2.0 - 1.0


def default_strategies() -> list[Strategy]:
    """The strategies of the research (see the module): trend, relative momentum, moving averages, mean reversion and
    carry on the asset groups and the currencies, and currency hedging for each investor currency.
    """

    def both_sides(**kwargs) -> list[Strategy]:
        return [Strategy(**kwargs), Strategy(**kwargs, long_short=True)]

    strategies = []
    for universe in [*ASSET_GROUPS, CURRENCIES_UNIVERSE]:
        u = {'universe': universe}
        for months, skip in [(1, 0), (3, 0), (6, 0), (12, 0), (12, 1)]:
            name = f'Momentum {months}' + (f'-{skip}' if skip else '') + 'M'
            signal = lambda x, m=months, s=skip: signals.momentum(x.index, m, s)  # noqa: E731
            strategies += both_sides(family='Trend', name=name, signal=signal, **u)
        for months, skip in [(3, 0), (6, 0), (12, 1)]:
            name = f'Momentum {months}' + (f'-{skip}' if skip else '') + 'M'
            signal = lambda x, m=months, s=skip: signals.momentum(x.index, m, s)  # noqa: E731
            strategies += both_sides(family='Relative momentum', name=name, signal=signal, construction='xs', **u)
        for fast, slow in [(1, 210), (20, 100), (50, 200)]:
            name = 'Price vs 10-month average' if fast == 1 else f'Moving averages {fast}/{slow}'
            signal = lambda x, f=fast, s=slow: signals.ma_crossover(x.index, f, s)  # noqa: E731
            strategies += both_sides(family='Moving averages', name=name, signal=signal, **u)
        reversal_1m = lambda x: signals.reversal(x.index, 21)  # noqa: E731
        strategies += both_sides(family='Mean reversion', name='Reversal 1M', signal=reversal_1m, **u)
        strategies += both_sides(
            family='Mean reversion', name='Reversal 1M', signal=reversal_1m, construction='xs', **u
        )
        strategies += both_sides(
            family='Mean reversion',
            name='Reversal 1W',
            signal=lambda x: signals.reversal(x.index, 5),
            construction='xs',
            freq='W-FRI',
            **u,
        )
        strategies += both_sides(
            family='Mean reversion',
            name='Z-score 60D below -1',
            signal=lambda x: signals.zscore_reversal(x.index, 60),
            threshold=1.0,
            **u,
        )
        strategies += both_sides(
            family='Mean reversion',
            name='Long-term reversal 36-12M',
            signal=lambda x: -signals.momentum(x.index, 36, 12),
            construction='xs',
            **u,
        )
    for universe in ['Bonds', CURRENCIES_UNIVERSE]:
        name = 'Yield curve slope' if universe == 'Bonds' else 'Interest rate gap'
        for construction in ['ts', 'xs']:
            strategies += both_sides(
                family='Carry', name=name, universe=universe, signal=_carry, construction=construction
            )
    for home in INVESTOR_CURRENCIES:
        u = {'family': 'Currency hedging', 'universe': HEDGING_PREFIX + home}
        strategies += [
            Strategy(name='Unhedged while carry > 0', signal=_carry, **u),
            Strategy(name='Unhedged while 12M trend > 0', signal=lambda x: signals.momentum(x.index, 12), **u),
            Strategy(
                name='Unhedged above 10-month average', signal=lambda x: signals.ma_crossover(x.index, 1, 210), **u
            ),
            Strategy(name='Unhedged while carry and trend > 0', signal=_carry_and_trend, **u),
        ]
    return strategies


def strategy_weights(strategy: Strategy, universe: Universe) -> pd.DataFrame:
    """Target weights of a strategy on its signal days."""
    days = signal_days(universe.excess.index, strategy.freq)
    score = strategy.signal(universe).reindex(days)
    eligible = universe.index.reindex(days).notna() & score.notna()
    budget = budgets(eligible, universe.vol.reindex(days) if strategy.risk_parity else None)
    if strategy.construction == 'xs':
        return cross_section_weights(score, budget, long_short=strategy.long_short)
    return time_series_weights(score, budget, long_short=strategy.long_short, threshold=strategy.threshold)


def benchmark_weights(universe: Universe, freq: str = 'M') -> pd.DataFrame:
    """Every instrument with an excess return held in equal shares (never hedged, for currency hedging)."""
    days = signal_days(universe.excess.index, freq)
    return budgets(universe.index.reindex(days).notna())


@dataclass
class StrategyResults:
    """Backtests of strategies and of the benchmarks of their universes, with their summaries."""

    universes: dict[str, Universe]
    backtests: dict[Strategy, BacktestResult]
    benchmarks: dict[str, BacktestResult]

    def table(self) -> pd.DataFrame:
        """One row per strategy: its summary, and the excess return and Sharpe ratio of its benchmark over the same
        days (cash for long/short strategies: 0).
        """
        rows = []
        for strategy, result in self.backtests.items():
            row = {
                'family': strategy.family,
                'universe': strategy.universe,
                'strategy': strategy.label,
                'start': result.returns.index[0] if len(result.returns) else pd.NaT,
                **summary(result),
            }
            if strategy.long_short:
                row['benchmark_return'], row['benchmark_sharpe'] = 0.0, np.nan
            else:
                bench = self.benchmarks[strategy.universe].returns.reindex(result.returns.index).fillna(0.0)
                stats = summary(BacktestResult(bench, pd.DataFrame(), pd.Series(dtype=float)))
                row['benchmark_return'], row['benchmark_sharpe'] = stats.get('excess_return'), stats.get('sharpe')
            rows.append(row)
        return pd.DataFrame(rows)


def run_strategies(strategies: list[Strategy] | None = None, data: ResearchData | None = None) -> StrategyResults:
    """Backtest strategies (the default ones if none are given) on the research data (loaded if not given)."""
    data = data or load_research_data()
    universes = build_universes(data)
    strategies = strategies or default_strategies()
    backtests = {
        s: run_backtest(strategy_weights(s, universes[s.universe]), universes[s.universe].excess) for s in strategies
    }
    benchmarks = {
        name: run_backtest(benchmark_weights(universes[name]), universes[name].excess)
        for name in {s.universe for s in strategies}
    }
    return StrategyResults(universes, backtests, benchmarks)
