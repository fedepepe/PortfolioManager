"""Out-of-sample evaluation of the research: are the results still there with choices made without hindsight?

Variants of the same idea compete (e.g. the momentum lookbacks of trend on equity indices, long-only): a family of
strategies sharing their universe, construction and side.

- Walk-forward: on the first business day of each year, the variant with the best skill so far (at least MIN_YEARS of
  history) is chosen and followed for the year; only those years count.
- Combination: all the variants held at once in equal parts, no choice at all.
- Stability: the skill of each strategy in the first and in the second half of its history.

The skill is measured as in statistics: the Sharpe ratio of the returns beyond the exposure to the benchmark for
long-only strategies (currency hedging included), of the excess returns for long/short ones.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from portfolio_manager.research.backtest import DAYS_PER_YEAR, BacktestResult, summary
from portfolio_manager.research.statistics import alpha_regression, mean_test, monthly_returns
from portfolio_manager.research.strategies import Strategy, StrategyResults

MIN_YEARS = 3  # history needed before a variant can be chosen


def skill_returns(returns: pd.Series, benchmark: pd.Series | None) -> pd.Series:
    """Daily returns beyond the exposure to the benchmark (beta from the same days), or the returns themselves without a
    benchmark.
    """
    if benchmark is None:
        return returns
    bench = benchmark.reindex(returns.index).fillna(0.0)
    variance = bench.var()
    beta = returns.cov(bench) / variance if variance > 0 else 0.0
    return returns - beta * bench


def skill_sharpe(returns: pd.Series, benchmark: pd.Series | None) -> float:
    """Annualized Sharpe ratio of the skill returns."""
    skill = skill_returns(returns, benchmark)
    std = skill.std()
    return float(skill.mean() / std * np.sqrt(DAYS_PER_YEAR)) if std > 1e-12 else np.nan


def history_years(returns: pd.Series) -> float:
    """Years from the first to the last known return."""
    known = returns.dropna()
    return (known.index[-1] - known.index[0]).days / 365.25 if len(known) else 0.0


def family_key(strategy: Strategy) -> tuple[str, str, str, bool]:
    """The family of a strategy: the variants of the same idea on the same universe, built and traded the same way."""
    return strategy.family, strategy.universe, strategy.construction, strategy.long_short


@dataclass
class FamilyResult:
    """Out-of-sample results of a family: the walk-forward returns and the variant chosen each year, and the returns of
    the equal-weight combination.
    """

    walk_forward: pd.Series
    choices: pd.Series
    combination: pd.Series


def walk_forward(variants: dict[Strategy, pd.Series], benchmark: pd.Series | None) -> tuple[pd.Series, pd.Series]:
    """Returns of following, each year, the variant with the best skill so far, and the variant chosen by year."""
    returns = pd.DataFrame(variants)
    years = sorted(set(returns.index.year))
    pieces, choices = [], {}
    for year in years:
        past = returns[returns.index.year < year]
        candidates = {
            s: skill_sharpe(past[s].dropna(), benchmark)
            for s in returns.columns
            if history_years(past[s]) >= MIN_YEARS - 0.01
        }
        candidates = {s: v for s, v in candidates.items() if not np.isnan(v)}
        if not candidates:
            continue
        best = max(candidates, key=candidates.get)
        choices[year] = best.name
        pieces.append(returns.loc[returns.index.year == year, best].dropna())
    if not pieces:
        return pd.Series(dtype=float), pd.Series(dtype=object)
    return pd.concat(pieces), pd.Series(choices)


def combination(variants: dict[Strategy, pd.Series]) -> pd.Series:
    """Returns of holding every variant available in equal parts (rebalanced daily between the variants)."""
    return pd.DataFrame(variants).mean(axis=1, skipna=True).dropna()


def evaluate_families(results: StrategyResults) -> dict[tuple, FamilyResult]:
    """Walk-forward and combination of each family with at least two variants."""
    families: dict[tuple, dict[Strategy, pd.Series]] = {}
    for strategy, result in results.backtests.items():
        if len(result.returns):
            families.setdefault(family_key(strategy), {})[strategy] = result.returns
    evaluated = {}
    for key, variants in families.items():
        if len(variants) < 2:
            continue
        long_short = key[3]
        benchmark = None if long_short else results.benchmarks[key[1]].returns
        returns, choices = walk_forward(variants, benchmark)
        evaluated[key] = FamilyResult(returns, choices, combination(variants))
    return evaluated


def out_of_sample_row(returns: pd.Series, benchmark: pd.Series | None) -> dict:
    """Summary of out-of-sample returns: years, excess return, Sharpe ratio and, against the benchmark, alpha (per
    year), its t-statistic and the beta (for long/short: the t-statistic of the mean excess return).
    """
    if len(returns) < DAYS_PER_YEAR:
        empty = dict.fromkeys(['excess_return', 'sharpe', 'alpha', 'beta', 'skill_t'], np.nan)
        return {'years': len(returns) / DAYS_PER_YEAR, **empty}
    stats = summary(BacktestResult(returns, pd.DataFrame(), pd.Series(dtype=float)))
    row = {'years': stats['years'], 'excess_return': stats['excess_return'], 'sharpe': stats['sharpe']}
    monthly = monthly_returns(returns)
    if benchmark is None:
        row.update(alpha=np.nan, beta=np.nan, skill_t=mean_test(monthly).t_stat)
    else:
        fit = alpha_regression(monthly, monthly_returns(benchmark.reindex(returns.index).fillna(0.0)))
        row.update(alpha=(1.0 + fit.alpha) ** 12 - 1.0, beta=fit.beta, skill_t=fit.t_stat)
    return row


def family_table(results: StrategyResults, evaluated: dict[tuple, FamilyResult]) -> pd.DataFrame:
    """One row per family: its best variant in sample (by skill), then the walk-forward and the combination out of
    sample.
    """
    rows = []
    for key, family in evaluated.items():
        family_name, universe, construction, long_short = key
        benchmark = None if long_short else results.benchmarks[universe].returns
        variants = {s: r for s, r in results.backtests.items() if family_key(s) == key and len(r.returns)}
        best = max(variants, key=lambda s: np.nan_to_num(skill_sharpe(variants[s].returns, benchmark), nan=-np.inf))
        row = {
            'family': family_name,
            'universe': universe,
            'construction': construction.upper(),
            'side': 'long/short' if long_short else 'long-only',
            'variants': len(variants),
            'best_in_sample': best.name,
            'best_skill_sharpe': skill_sharpe(variants[best].returns, benchmark),
        }
        for prefix, returns in [('wf_', family.walk_forward), ('combo_', family.combination)]:
            row.update({prefix + k: v for k, v in out_of_sample_row(returns, benchmark).items()})
        row['wf_choices'] = ', '.join(f'{year}: {name}' for year, name in family.choices.items())
        rows.append(row)
    return pd.DataFrame(rows)


def stability(results: StrategyResults) -> pd.DataFrame:
    """Skill Sharpe ratio of each strategy in the first and in the second half of its history (rows in the order of
    results.table()).
    """
    rows = []
    for strategy, result in results.backtests.items():
        benchmark = None if strategy.long_short else results.benchmarks[strategy.universe].returns
        r = result.returns
        half = len(r) // 2
        rows.append(
            {
                'skill_sharpe_first_half': skill_sharpe(r.iloc[:half], benchmark) if half else np.nan,
                'skill_sharpe_second_half': skill_sharpe(r.iloc[half:], benchmark) if half else np.nan,
            }
        )
    return pd.DataFrame(rows)
