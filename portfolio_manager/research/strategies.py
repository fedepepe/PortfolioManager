"""Strategies of the research: universes of instruments, the signals applied to them and their backtests.

A universe is a group of instruments (equity indices, sectors, bonds, commodities), the stocks of an index (today's
members only: the companies that left the index are missing, which flatters long-only results), the currencies against
the US dollar, or the currencies an investor holds against their own (currency hedging: a currency is left unhedged
while the strategy holds it, hedged otherwise). Each strategy turns a signal into weights on every signal day (time
series: each instrument on its own; cross section: the best ranked ones), long-only or long/short, and is compared with
the benchmark of its universe: every instrument held in equal shares (long-only), never hedged (currency hedging), or
cash (long/short).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
import pandas as pd

from portfolio_manager.market_data.index_members import STOCK_INDICES
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
from portfolio_manager.research.returns import ResearchData, load_research_data, stock_group
from portfolio_manager.storage.queries import query_interest_rates

ASSET_GROUPS = ['Equity indices', 'US sectors', 'European sectors', 'Bonds', 'Commodities']
STOCK_UNIVERSES = [stock_group(index.name) for index in STOCK_INDICES]
SECTOR_NEUTRAL_UNIVERSES = [stock_group('S&P 500')]  # enough stocks in each sector
STOCK_FRACTION = 0.2  # stocks: the top (and bottom) fifth
MIN_MEMBER_COVERAGE = 0.5  # share of today's members with prices from which the stocks of an index are studied
CURRENCIES_UNIVERSE = 'Currencies vs USD'
HEDGING_PREFIX = 'Hedging '  # + the investor's currency
INVESTOR_CURRENCIES = ['CHF', 'EUR']  # of the accounts
HEDGED_CURRENCIES = ['USD', 'EUR', 'GBP', 'CHF']  # the foreign currencies of their holdings
# 10-year government yield of each currency: with the cash rate, the slope of the yield curve (the carry of bonds)
LONG_YIELDS = {'USD': 'USD_GOV_10Y', 'EUR': 'EUR_GOV_10Y', 'CHF': 'CHF_GOV_10Y', 'GBP': 'GBP_GOV_10Y'}


@dataclass
class Universe:
    """Instruments traded together: their daily excess returns, their carry (if any), their sectors (stocks) and the
    kind of benchmark.
    """

    name: str
    excess: pd.DataFrame
    carry: pd.DataFrame | None = None
    hedging: bool = False  # currency hedging: the benchmark is never hedged
    sectors: pd.Series | None = None  # by ticker

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
    """A signal applied to a universe: time series (ts) or cross section (xs, the top fraction; sector-neutral: ranked
    within each sector), long-only or long/short, on the last business day of each period (freq), optionally in equal
    risk shares.
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
    fraction: float = 1 / 3
    sector_neutral: bool = False

    @property
    def label(self) -> str:
        """Name, construction and side, e.g. 'Momentum 12M (TS, long-only)'."""
        side = 'long/short' if self.long_short else 'long-only'
        neutral = ', sector-neutral' if self.sector_neutral else ''
        return f'{self.name} ({self.construction.upper()}{neutral}, {side})'


def term_spreads(data: ResearchData, instruments: pd.DataFrame) -> pd.DataFrame:
    """Slope of the yield curve of the currency of each instrument: 10-year yield minus the cash rate (decimals)."""
    yields = query_interest_rates(list(LONG_YIELDS.values()))
    yields = yields.reindex(yields.index.union(data.prices.index)).ffill().reindex(data.prices.index)
    spreads = {}
    for ticker, currency in instruments['currency'].items():
        if currency in LONG_YIELDS and LONG_YIELDS[currency] in yields:
            spreads[ticker] = yields[LONG_YIELDS[currency]] - data.cash_rates[currency]
    return pd.DataFrame(spreads, index=data.prices.index)


def stock_history(excess: pd.DataFrame) -> pd.DataFrame:
    """Excess returns of the stocks of an index from the first day at least MIN_MEMBER_COVERAGE of today's members have
    one: earlier, the few stocks with prices are the ones that went on to join the index (survivorship bias at its
    worst).
    """
    covered = excess.notna().mean(axis=1) >= MIN_MEMBER_COVERAGE
    return excess.loc[covered.idxmax() :] if covered.any() else excess.iloc[:0]


def build_universes(data: ResearchData) -> dict[str, Universe]:
    """The universes of the research data (see the module)."""
    excess = data.excess_returns('B')
    universes = {}
    for group in ASSET_GROUPS:
        tickers = data.instruments.index[data.instruments['group'] == group]
        carry = term_spreads(data, data.instruments.loc[tickers]) if group == 'Bonds' else None
        universes[group] = Universe(group, excess[tickers], carry)
    for group in STOCK_UNIVERSES:
        stocks = data.instruments[data.instruments['group'] == group]
        if len(stocks):
            universes[group] = Universe(group, stock_history(excess[stocks.index]), sectors=stocks['sector'])
    currencies = CurrencyIndices(data)
    available = currencies.currencies()
    others = [c for c in available if c != 'USD']
    if others:
        universes[CURRENCIES_UNIVERSE] = Universe(
            CURRENCIES_UNIVERSE, currencies.excess_returns(others, 'USD'), currencies.carry(others, 'USD')
        )
    for home in INVESTOR_CURRENCIES:
        foreign = [c for c in HEDGED_CURRENCIES if c != home and c in available]
        if home not in available or not foreign:
            continue
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
    strategies += stock_strategies()
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


def stock_strategies() -> list[Strategy]:
    """Cross-sectional strategies on the stocks of each index, long-only and long/short (the top and bottom fifth):
    relative momentum, short- and long-term reversal, low volatility and closeness to the 52-week high; sector-neutral
    versions where every sector has enough stocks.
    """
    specs = [
        ('Relative momentum', 'Momentum 12-1M', lambda x: signals.momentum(x.index, 12, 1)),
        ('Relative momentum', 'Momentum 6-1M', lambda x: signals.momentum(x.index, 6, 1)),
        ('Mean reversion', 'Reversal 1M', lambda x: signals.reversal(x.index, 21)),
        ('Mean reversion', 'Long-term reversal 36-12M', lambda x: -signals.momentum(x.index, 36, 12)),
        ('Low volatility', 'Low volatility 12M', lambda x: signals.low_volatility(x.excess, 12)),
        ('52-week high', 'Near the 52-week high', lambda x: signals.high_proximity(x.index)),
    ]
    strategies = []
    for universe in STOCK_UNIVERSES:
        for neutral in [False, True] if universe in SECTOR_NEUTRAL_UNIVERSES else [False]:
            for family, name, signal in specs:
                for long_short in [False, True]:
                    strategies.append(
                        Strategy(
                            family=family,
                            name=name,
                            universe=universe,
                            signal=signal,
                            construction='xs',
                            long_short=long_short,
                            fraction=STOCK_FRACTION,
                            sector_neutral=neutral,
                        )
                    )
    return strategies


def within_sectors(score: pd.DataFrame, sectors: pd.Series) -> pd.DataFrame:
    """Rank of each score among the stocks of its sector each day, from 0 to 1 (a stock without sector keeps none)."""
    ranks = [
        score[tickers].rank(axis=1, pct=True)
        for _, tickers in sectors.reindex(score.columns).dropna().groupby(lambda t: sectors[t]).groups.items()
    ]
    return pd.concat(ranks, axis=1).reindex(columns=score.columns) if ranks else score * np.nan


def strategy_weights(strategy: Strategy, universe: Universe) -> pd.DataFrame:
    """Target weights of a strategy on its signal days."""
    days = signal_days(universe.excess.index, strategy.freq)
    score = strategy.signal(universe).reindex(days)
    if strategy.sector_neutral and universe.sectors is not None:
        score = within_sectors(score, universe.sectors)
    eligible = universe.index.reindex(days).notna() & score.notna()
    budget = budgets(eligible, universe.vol.reindex(days) if strategy.risk_parity else None)
    if strategy.construction == 'xs':
        return cross_section_weights(score, budget, fraction=strategy.fraction, long_short=strategy.long_short)
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
    """Backtest strategies (the default ones if none are given) on the research data (loaded if not given); those of a
    universe without instruments (e.g. no stocks stored) are left out.
    """
    data = data or load_research_data()
    universes = build_universes(data)
    strategies = [s for s in strategies or default_strategies() if s.universe in universes]
    backtests = {
        s: run_backtest(strategy_weights(s, universes[s.universe]), universes[s.universe].excess) for s in strategies
    }
    benchmarks = {
        name: run_backtest(benchmark_weights(universes[name]), universes[name].excess)
        for name in {s.universe for s in strategies}
    }
    return StrategyResults(universes, backtests, benchmarks)
