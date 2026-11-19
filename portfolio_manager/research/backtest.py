"""Fast backtest of the research strategies on excess returns: target weights decided at the close of each signal day,
traded at the close TRADE_LAG business days later, then held (drifting with the returns) until the next trade.

The positions are hedged into the investor's currency: a long position earns the excess return of its instrument, a
short one its opposite, and the rest earns cash (no excess). The result is the excess return of the strategy over cash,
for any currency. Each trade costs COST_RATE of the traded amount.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

COST_RATE = 0.001  # cost of a trade, as a fraction of the traded amount (0.10%)
TRADE_LAG = 1  # business days between the signal and the trade
DAYS_PER_YEAR = 261  # business days


def signal_days(days: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    """The last business day of each period (M: month, W-FRI: week ending on Friday) within days."""
    periods = days.to_period(freq)
    return pd.DatetimeIndex(days.to_series().groupby(periods).max().to_numpy())


def budgets(eligible: pd.DataFrame, vol: pd.DataFrame | None = None) -> pd.DataFrame:
    """Share of the portfolio of each eligible instrument: equal, or inversely proportional to its volatility (equal
    risk); 0 for the others. The shares of each day add up to 1.
    """
    raw = eligible.astype(float) if vol is None else (1.0 / vol).where(eligible & (vol > 0), 0.0)
    raw = raw.fillna(0.0)
    total = raw.sum(axis=1)
    return raw.div(total.where(total > 0), axis=0).fillna(0.0)


def time_series_weights(
    score: pd.DataFrame, budget: pd.DataFrame, long_short: bool = False, threshold: float = 0.0
) -> pd.DataFrame:
    """Each instrument on its own: its share held when its score is above the threshold (long-only: in cash otherwise),
    or short when below minus the threshold (long/short).
    """
    long = (score > threshold).astype(float)
    if long_short:
        long = long - (score < -threshold).astype(float)
    return budget * long


def cross_section_weights(
    score: pd.DataFrame, budget: pd.DataFrame, fraction: float = 1 / 3, long_short: bool = False, min_count: int = 3
) -> pd.DataFrame:
    """The instruments ranked by score each day: the top fraction held (their shares rescaled to a full portfolio),
    and the bottom fraction short for long/short (a short portfolio of the same size); days with fewer than min_count
    eligible instruments stay in cash.
    """
    eligible = (budget > 0) & score.notna()
    count = eligible.sum(axis=1)
    k = np.ceil(count * fraction).clip(lower=1)
    ranks_top = score.where(eligible).rank(axis=1, ascending=False, method='first')
    ranks_bottom = score.where(eligible).rank(axis=1, ascending=True, method='first')
    enough = (count >= min_count).to_numpy()[:, None]

    def side(selected: pd.DataFrame) -> pd.DataFrame:
        shares = budget.where(selected & enough, 0.0)
        total = shares.sum(axis=1)
        return shares.div(total.where(total > 0), axis=0).fillna(0.0)

    weights = side(ranks_top.le(k, axis=0))
    if long_short:
        weights = weights - side(ranks_bottom.le(k, axis=0))
    return weights


@dataclass
class BacktestResult:
    """A backtest: the daily excess returns after costs (from the first trade), the target weights by signal day and
    the fraction of the portfolio traded at each trade.
    """

    returns: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series

    def nav(self) -> pd.Series:
        """Value of the strategy, 1 at the start."""
        return (1.0 + self.returns).cumprod()


def run_backtest(
    weights: pd.DataFrame, excess_returns: pd.DataFrame, cost_rate: float = COST_RATE, lag: int = TRADE_LAG
) -> BacktestResult:
    """Backtest target weights (signal day x ticker) on daily excess returns (see the module), from the first signal
    day with a position.
    """
    invested = weights.fillna(0.0).abs().sum(axis=1) > 0
    weights = weights.loc[invested.idxmax() :] if invested.any() else weights.iloc[:0]
    excess = excess_returns.reindex(columns=weights.columns)
    days = excess.index
    x = excess.fillna(0.0).to_numpy()
    targets = weights.fillna(0.0).to_numpy()
    trade_pos = days.get_indexer(weights.index) + lag
    keep = (trade_pos >= lag) & (trade_pos < len(days))
    trade_pos, targets = trade_pos[keep], targets[keep]
    returns = np.zeros(len(days))
    turnover = np.zeros(len(trade_pos))
    held = np.zeros(x.shape[1])  # weights after the drift since the last trade
    for n, start in enumerate(trade_pos):
        end = trade_pos[n + 1] if n + 1 < len(trade_pos) else len(days) - 1
        target = targets[n]
        turnover[n] = np.abs(target - held).sum()
        returns[start] = (1.0 + returns[start]) * (1.0 - cost_rate * turnover[n]) - 1.0
        if end > start:
            growth = np.cumprod(1.0 + x[start + 1 : end + 1], axis=0)
            value = 1.0 + (growth - 1.0) @ target  # value of the portfolio since the trade, 1 at the trade
            returns[start + 1 : end + 1] = value / np.concatenate([[1.0], value[:-1]]) - 1.0
            held = target * growth[-1] / value[-1] if value[-1] != 0 else np.zeros_like(target)
        else:
            held = target
    first = trade_pos[0] if len(trade_pos) else len(days)
    return BacktestResult(
        returns=pd.Series(returns[first:], index=days[first:]),
        weights=weights,
        turnover=pd.Series(turnover, index=days[trade_pos]),
    )


def summary(result: BacktestResult) -> dict[str, float]:
    """Annualized excess return (compounded), volatility, Sharpe ratio, maximum drawdown, turnover per year and years
    of history of a backtest.
    """
    r = result.returns
    years = len(r) / DAYS_PER_YEAR
    if years == 0:
        return {}
    nav = (1.0 + r).cumprod()
    vol = r.std() * np.sqrt(DAYS_PER_YEAR)
    return {
        'excess_return': nav.iloc[-1] ** (1 / years) - 1.0,
        'volatility': vol,
        'sharpe': r.mean() * DAYS_PER_YEAR / vol if vol > 0 else np.nan,
        'max_drawdown': (1.0 - nav / nav.cummax()).max(),
        'turnover': result.turnover.sum() / years,
        'years': years,
    }
