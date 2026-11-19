"""Survivorship bias of the stock research: the S&P 500 with the members of each date instead of today's.

Today's members, studied over the past, include the companies that later joined the index (held during the rise that got
them in) and leave out those that left it (taken over, bankrupt, demoted): the equal-weight portfolio of today's members
beats the real index by several points a year. With the changes of the index (Wikipedia, complete enough from
CHANGES_RELIABLE_FROM), the members of each date are rebuilt by undoing the changes from today's list backwards; a stock
can then be held only while it is a member (its earlier prices still feed the signals).

Yahoo Finance has the prices of only part of the former members (demoted companies still trade; those taken over or
bankrupt are gone, and some tickers now belong to other companies): a former member is usable only if its prices cover
MIN_PRICE_COVERAGE of its days as a member. The missing ones are bracketed: while members they are assumed to earn as
the others, and on their removal they lose nothing (upper bound) or everything (lower bound). The equal-weight ETF of
the real index (RSP, after its fees) shows how much bias is left.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from portfolio_manager.market_data.history import load_adjusted_closes
from portfolio_manager.market_data.index_members import CHANGES_RELIABLE_FROM, EQUAL_WEIGHT_REFERENCE
from portfolio_manager.research.backtest import DAYS_PER_YEAR, BacktestResult, summary
from portfolio_manager.research.returns import ResearchData, deposit_growth, stock_group
from portfolio_manager.storage.queries import query_index_changes, query_index_members

MIN_PRICE_COVERAGE = 0.9  # share of its days as a member a former member needs prices for
INDEX = 'S&P 500'
POINT_IN_TIME_UNIVERSE = f'{stock_group(INDEX)} (point-in-time)'


def point_in_time_members(current: Iterable[str], changes: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Members of an index on each day (date x ticker, True for a member): today's members, with the changes effective
    after each day undone (the added stock removed, the removed one put back). changes: date, added, removed.
    """
    changes = changes.sort_values('date', ascending=False)
    tickers = sorted(set(current) | set(changes['added'].dropna()) | set(changes['removed'].dropna()))
    column = {t: n for n, t in enumerate(tickers)}
    mask = np.zeros((len(days), len(tickers)), dtype=bool)
    members = set(current)
    pending = list(changes[['date', 'added', 'removed']].itertuples(index=False))
    k = 0
    for i in range(len(days) - 1, -1, -1):
        while k < len(pending) and pending[k].date > days[i]:
            if pending[k].added:
                members.discard(pending[k].added)
            if pending[k].removed:
                members.add(pending[k].removed)
            k += 1
        mask[i, [column[t] for t in members]] = True
    return pd.DataFrame(mask, index=days, columns=tickers)


def usable_tickers(members: pd.DataFrame, excess: pd.DataFrame, min_coverage: float = MIN_PRICE_COVERAGE) -> pd.Series:
    """Share of its days as a member each stock has prices for (0 without prices), by ticker."""
    priced = excess.reindex(index=members.index, columns=members.columns).notna()
    member_days = members.sum()
    return ((members & priced).sum() / member_days.where(member_days > 0)).fillna(0.0)


@dataclass
class PointInTime:
    """The members of each day (date x ticker) of the stocks with usable prices, and of all the members, from the start
    of the reliable changes; the price coverage of each member.
    """

    members: pd.DataFrame
    all_members: pd.DataFrame
    coverage: pd.Series

    @property
    def missing(self) -> pd.Index:
        """Members without usable prices."""
        return self.all_members.columns.difference(self.members.columns)


def point_in_time(data: ResearchData, start: pd.Timestamp = CHANGES_RELIABLE_FROM) -> PointInTime | None:
    """The point-in-time S&P 500 of the research data (None without stored changes)."""
    changes = query_index_changes(INDEX)
    if changes.empty:
        return None
    days = data.prices.index[data.prices.index >= start]
    current = query_index_members(INDEX)['ticker']
    all_members = point_in_time_members(current, changes, days)
    all_members = all_members.loc[:, all_members.any()]
    excess = data.excess_returns('B')
    coverage = usable_tickers(all_members, excess)
    # today's members are always usable (without prices before their listing they are simply not held then)
    usable = [t for t in all_members.columns if t in set(current) or coverage[t] >= MIN_PRICE_COVERAGE]
    return PointInTime(all_members[usable], all_members, coverage)


def equal_weight_returns(excess: pd.DataFrame, members: pd.DataFrame) -> pd.Series:
    """Daily excess returns of the members of each day in equal parts (rebalanced daily: a reference, not traded)."""
    held = members.shift(1, fill_value=False)  # yesterday's members earn today's returns
    returns = excess.reindex(index=members.index, columns=members.columns).where(held)
    return returns.mean(axis=1).fillna(0.0)


def exit_losses(pit: PointInTime) -> pd.Series:
    """Daily loss of the equal-weight index if each missing member lost everything on its removal: its share (1 / the
    number of members) on the day it leaves the index.
    """
    counts = pit.all_members.sum(axis=1)
    leaving = pit.all_members[pit.missing].astype(int).diff(-1).clip(lower=0)  # 1 on the last day as a member
    leaving = leaving.shift(1, fill_value=0)  # the loss falls on the next day
    return -(leaving.sum(axis=1) / counts.shift(1)).fillna(0.0)


def reference_excess(data: ResearchData, index: pd.DatetimeIndex) -> pd.Series:
    """Daily excess returns of the equal-weight ETF of the real index over dollar cash (empty if not stored)."""
    ticker = EQUAL_WEIGHT_REFERENCE[INDEX]
    closes = load_adjusted_closes([ticker])
    if ticker not in closes:
        return pd.Series(dtype=float)
    cash = deposit_growth(data.cash_rates['USD'], 360)
    prices = closes[ticker].reindex(closes.index.union(index)).ffill().reindex(index)
    return (prices / cash.reindex(index)).pct_change(fill_method=None)


def annual(returns: pd.Series) -> float:
    """Compounded excess return per year of daily returns."""
    r = returns.dropna()
    return (1.0 + r).prod() ** (DAYS_PER_YEAR / len(r)) - 1.0 if len(r) else np.nan


def benchmark_table(data: ResearchData, pit: PointInTime, todays_members: pd.Series) -> pd.DataFrame:
    """Excess return per year of the equal-weight S&P 500 over the same days: today's members (the research's
    benchmark), the point-in-time members with prices, the same if the missing ones lost everything on their removal,
    and the real index (RSP, after its 0.20% fees).
    """
    excess = data.excess_returns('B')
    point_in_time_returns = equal_weight_returns(excess, pit.members)
    days = point_in_time_returns.index[1:]
    series = {
        "Today's members": todays_members.reindex(days),
        'Point-in-time members with prices': point_in_time_returns.reindex(days),
        'Point-in-time, missing members lost on removal': (point_in_time_returns + exit_losses(pit)).reindex(days),
        'Real index (RSP)': reference_excess(data, days).reindex(days),
    }
    real = annual(series['Real index (RSP)'])
    return pd.DataFrame(
        {
            'excess_return': {name: annual(r) for name, r in series.items()},
            'gap_to_real_index': {name: annual(r) - real for name, r in series.items()},
        }
    ).rename_axis(f'{days[0]:%Y-%m-%d} .. {days[-1]:%Y-%m-%d}')


def coverage_table(pit: PointInTime) -> pd.DataFrame:
    """How much of the point-in-time index has prices: members on average, the share with usable prices, former
    members found and missing.
    """
    current = set(query_index_members(INDEX)['ticker'])
    former = [t for t in pit.all_members.columns if t not in current]
    usable_share = pit.members.sum(axis=1) / pit.all_members.sum(axis=1)
    rows = {
        'members on average': pit.all_members.sum(axis=1).mean(),
        'share of the members with prices, average': usable_share.mean(),
        'share of the members with prices, lowest (start)': usable_share.min(),
        'former members (removed since the start)': len(former),
        'former members with usable prices': sum(t in pit.members.columns for t in former),
        'former members without prices': sum(pit.coverage.get(t, 0.0) == 0.0 for t in former),
        'former members with prices of another period (reused ticker)': sum(
            0.0 < pit.coverage.get(t, 0.0) < MIN_PRICE_COVERAGE for t in former
        ),
    }
    return pd.Series(rows, name='value').to_frame()


def trimmed(result: BacktestResult, start: pd.Timestamp) -> BacktestResult:
    """A backtest from a day on."""
    return BacktestResult(result.returns[result.returns.index >= start], result.weights, result.turnover)


def strategy_comparison(results, start: pd.Timestamp) -> pd.DataFrame:
    """Each S&P 500 stock strategy on today's members and on the point-in-time members over the same days: Sharpe
    ratio, excess return and skill t-statistic (alpha against the benchmark of its universe for long-only strategies,
    mean excess return for long/short ones).
    """
    from portfolio_manager.research.statistics import alpha_regression, mean_test, monthly_returns

    def skill(strategy, result: BacktestResult) -> float:
        monthly = monthly_returns(result.returns)
        if strategy.long_short:
            return mean_test(monthly).t_stat
        bench = results.benchmarks[strategy.universe].returns.reindex(result.returns.index).fillna(0.0)
        return alpha_regression(monthly, monthly_returns(bench)).t_stat

    rows = []
    for strategy, result in results.backtests.items():
        if strategy.universe != stock_group(INDEX):
            continue
        twin = replace(strategy, universe=POINT_IN_TIME_UNIVERSE)
        if twin not in results.backtests:
            continue
        mine, theirs = trimmed(result, start), trimmed(results.backtests[twin], start)
        common = mine.returns.index.intersection(theirs.returns.index)
        mine = BacktestResult(mine.returns.reindex(common), mine.weights, mine.turnover)
        theirs = BacktestResult(theirs.returns.reindex(common), theirs.weights, theirs.turnover)
        a, b = summary(mine), summary(theirs)
        if not a or not b:
            continue
        rows.append(
            {
                'family': strategy.family,
                'strategy': strategy.label,
                'todays_excess_return': a['excess_return'],
                'pit_excess_return': b['excess_return'],
                'todays_sharpe': a['sharpe'],
                'pit_sharpe': b['sharpe'],
                'todays_skill_t': skill(strategy, mine),
                'pit_skill_t': skill(twin, theirs),
            }
        )
    table = pd.DataFrame(rows)
    if not table.empty:
        table['skill_t_change'] = table['pit_skill_t'] - table['todays_skill_t']
    return table
