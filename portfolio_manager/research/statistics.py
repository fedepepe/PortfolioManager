"""Statistical tests of the research: do the signals predict the returns, and are the results of the strategies more
than luck?

- Time-series regressions: the excess return of each instrument over the next period on its signal, instrument by
  instrument (Newey-West standard errors) and pooled over the universe (each instrument's signal and returns scaled by
  their standard deviations; standard errors clustered by date).
- Fama-MacBeth: each period, the returns of the instruments regressed on their signals (standardized across the
  instruments); the test is on the average slope (Newey-West).
- Strategies: the skill of a long-only strategy (or of currency hedging) is its alpha, the intercept of its monthly
  returns regressed on those of its benchmark (what it earns beyond its average exposure: a trend strategy holds less
  and earns less, but may lose much less); the skill of a long/short strategy is its mean excess return. Both are
  t-tested (Newey-West); the p-values are adjusted for the number of strategies tried (Holm), and the deflated Sharpe
  ratio is the probability that the Sharpe ratio of the skill returns is above what the best of as many strategies
  without skill would show.
- Uncovered interest parity (Fama regression): the change of each exchange rate against the dollar on the interest
  rate gap. Parity predicts a slope of 1 (the higher rate is offset by a depreciation); a slope below 1 is the gain of
  carry.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.stats.multitest import multipletests

from portfolio_manager.research.backtest import signal_days
from portfolio_manager.research.strategies import (
    CURRENCIES_UNIVERSE,
    Strategy,
    StrategyResults,
    Universe,
    within_sectors,
)

NW_LAGS = 3  # lags of the Newey-West standard errors of monthly series
MIN_OBSERVATIONS = 24  # periods needed for a regression
EULER_GAMMA = 0.5772156649


@dataclass
class Regression:
    """Slope of a regression, its t-statistic and p-value (two-sided), and the number of observations."""

    slope: float
    t_stat: float
    p_value: float
    n_obs: int

    @classmethod
    def empty(cls) -> 'Regression':
        """No regression (too few observations)."""
        return cls(np.nan, np.nan, np.nan, 0)


def _ols(y: np.ndarray, x: np.ndarray, **fit_kwargs) -> Regression:
    if len(y) < MIN_OBSERVATIONS or np.nanstd(x) == 0:
        return Regression.empty()
    fit = sm.OLS(y, sm.add_constant(x, has_constant='add')).fit(**fit_kwargs)
    return Regression(float(fit.params[1]), float(fit.tvalues[1]), float(fit.pvalues[1]), len(y))


def mean_test(values: pd.Series, lags: int = NW_LAGS) -> Regression:
    """t-test of the mean of a series (Newey-West): the slope is the mean."""
    values = values.dropna()
    if len(values) < MIN_OBSERVATIONS:
        return Regression.empty()
    fit = sm.OLS(values.to_numpy(), np.ones(len(values))).fit(cov_type='HAC', cov_kwds={'maxlags': lags})
    return Regression(float(fit.params[0]), float(fit.tvalues[0]), float(fit.pvalues[0]), len(values))


def signal_and_forward_returns(
    signal: pd.DataFrame, index: pd.DataFrame, freq: str = 'M', horizon: int = 1
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The signal on the last business day of each period, and the excess return over the next horizon periods."""
    days = signal_days(index.index, freq)
    levels = index.reindex(days)
    return signal.reindex(days), levels.shift(-horizon) / levels - 1.0


def time_series_regressions(
    signal: pd.DataFrame, forward: pd.DataFrame, horizon: int = 1
) -> tuple[pd.DataFrame, Regression]:
    """Regression of the forward returns on the signal for each instrument (rows: slope, t_stat, p_value, n_obs) and
    pooled over all instruments.
    """
    rows, scaled = {}, []
    for ticker in signal.columns:
        pair = pd.concat([signal[ticker], forward[ticker]], axis=1, keys=['x', 'y']).dropna()
        lags = max(horizon, NW_LAGS)
        rows[ticker] = _ols(pair['y'].to_numpy(), pair['x'].to_numpy(), cov_type='HAC', cov_kwds={'maxlags': lags})
        if len(pair) >= MIN_OBSERVATIONS and pair.std().gt(0).all():
            scaled.append((pair - pair.mean()) / pair.std())
    per_instrument = pd.DataFrame({t: r.__dict__ for t, r in rows.items()}).T
    if not scaled:
        return per_instrument, Regression.empty()
    pooled = pd.concat(scaled)
    groups = pd.factorize(pooled.index)[0]
    return per_instrument, _ols(
        pooled['y'].to_numpy(), pooled['x'].to_numpy(), cov_type='cluster', cov_kwds={'groups': groups}
    )


def fama_macbeth(signal: pd.DataFrame, forward: pd.DataFrame, min_count: int = 3) -> Regression:
    """Average slope of the period-by-period regressions of the forward returns on the signal standardized across the
    instruments, its Newey-West t-statistic, and the number of periods.
    """
    slopes = {}
    for day in signal.index:
        pair = pd.concat([signal.loc[day], forward.loc[day]], axis=1, keys=['x', 'y']).dropna()
        if len(pair) < min_count or pair['x'].std() == 0:
            continue
        x = (pair['x'] - pair['x'].mean()) / pair['x'].std()
        slopes[day] = float(np.dot(x, pair['y'] - pair['y'].mean()) / np.dot(x, x))
    return mean_test(pd.Series(slopes, dtype=float))


def signal_test(strategy: Strategy, universe: Universe) -> Regression:
    """Whether the signal of a strategy predicts the next period's excess returns of its universe: pooled time-series
    regression for a time-series strategy, Fama-MacBeth for a cross-sectional one.
    """
    score = strategy.signal(universe)
    if strategy.sector_neutral and universe.sectors is not None:
        score = within_sectors(score, universe.sectors)
    signal, forward = signal_and_forward_returns(score, universe.index, strategy.freq)
    if strategy.construction == 'xs':
        return fama_macbeth(signal, forward)
    return time_series_regressions(signal, forward)[1]


def monthly_returns(daily: pd.Series) -> pd.Series:
    """Compounded monthly returns of daily returns."""
    return (1.0 + daily).resample('M').prod() - 1.0


def expected_max_sharpe(sharpe_variance: float, n_trials: int) -> float:
    """Expected highest Sharpe ratio of n_trials strategies without skill, whose estimates vary with sharpe_variance
    (Bailey and Lopez de Prado).
    """
    if n_trials < 2:
        return 0.0
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * np.e))
    return float(np.sqrt(sharpe_variance) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe(returns: pd.Series, benchmark_sharpe: float) -> float:
    """Probability that the true Sharpe ratio of the returns (per period) is above benchmark_sharpe, given their
    length, skewness and kurtosis.
    """
    r = returns.dropna()
    n = len(r)
    if n < MIN_OBSERVATIONS or r.std() == 0:
        return np.nan
    sr = r.mean() / r.std()
    skew, kurt = stats.skew(r), stats.kurtosis(r, fisher=False)
    denominator = np.sqrt(max(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2, 1e-12))
    return float(stats.norm.cdf((sr - benchmark_sharpe) * np.sqrt(n - 1) / denominator))


@dataclass
class Alpha:
    """Regression of a strategy's returns on its benchmark's: alpha per period, its t-statistic and p-value, the beta,
    and the skill returns (alpha plus the residuals: the returns beyond the exposure to the benchmark).
    """

    alpha: float
    t_stat: float
    p_value: float
    beta: float
    skill: pd.Series


def alpha_regression(returns: pd.Series, benchmark: pd.Series, lags: int = NW_LAGS) -> Alpha:
    """Alpha and beta of returns against a benchmark (Newey-West standard errors)."""
    pair = pd.concat([returns, benchmark], axis=1, keys=['y', 'x']).dropna()
    if len(pair) < MIN_OBSERVATIONS or pair['x'].std() == 0:
        return Alpha(np.nan, np.nan, np.nan, np.nan, pd.Series(dtype=float))
    fit = sm.OLS(pair['y'], sm.add_constant(pair['x'])).fit(cov_type='HAC', cov_kwds={'maxlags': lags})
    alpha, beta = float(fit.params.iloc[0]), float(fit.params.iloc[1])
    return Alpha(alpha, float(fit.tvalues.iloc[0]), float(fit.pvalues.iloc[0]), beta, pair['y'] - beta * pair['x'])


def one_sided(p_value: float, t_stat: float) -> float:
    """p-value of a positive effect from a two-sided p-value and its t-statistic."""
    return p_value / 2.0 if t_stat > 0 else 1.0 - p_value / 2.0 if not np.isnan(t_stat) else np.nan


def strategy_tests(results: StrategyResults) -> pd.DataFrame:
    """One row per strategy (in the order of results.table()): the test of its signal, of its mean monthly excess
    return, its alpha (per year) and beta against the benchmark (long-only), the one-sided p-value of its skill (see
    the module) and the same adjusted for all the strategies tried (Holm), and the deflated Sharpe ratio of its skill
    returns.
    """
    rows, skills = [], []
    for strategy, result in results.backtests.items():
        universe = results.universes[strategy.universe]
        returns = monthly_returns(result.returns) if len(result.returns) else pd.Series(dtype=float)
        excess = mean_test(returns)
        row = {'signal_t': signal_test(strategy, universe).t_stat, 'excess_t': excess.t_stat}
        if strategy.long_short:
            row.update(alpha=np.nan, alpha_t=np.nan, beta=np.nan, skill_p=one_sided(excess.p_value, excess.t_stat))
            skills.append(returns)
        else:
            bench = results.benchmarks[strategy.universe].returns.reindex(result.returns.index).fillna(0.0)
            fit = alpha_regression(returns, monthly_returns(bench) if len(bench) else returns)
            row.update(
                alpha=(1.0 + fit.alpha) ** 12 - 1.0,
                alpha_t=fit.t_stat,
                beta=fit.beta,
                skill_p=one_sided(fit.p_value, fit.t_stat),
            )
            skills.append(fit.skill)
        rows.append(row)
    table = pd.DataFrame(rows)
    valid = table['skill_p'].notna()
    table['skill_p_holm'] = np.nan
    if valid.any():
        table.loc[valid, 'skill_p_holm'] = multipletests(table.loc[valid, 'skill_p'], method='holm')[1]
    # deflated Sharpe ratio: against the best of as many strategies without skill
    sharpes = pd.Series([r.mean() / r.std() for r in skills if len(r.dropna()) >= MIN_OBSERVATIONS], dtype=float)
    threshold = expected_max_sharpe(float(sharpes.var()), len(sharpes)) if len(sharpes) > 1 else 0.0
    table['deflated_sharpe'] = [deflated_sharpe(r, threshold) for r in skills]
    return table


def uncovered_interest_parity(results: StrategyResults, horizon: int = 1) -> pd.DataFrame:
    """Fama regression for each currency against the dollar: the log change of its value in dollars over the next
    month on the interest rate gap over the month (dollar minus currency). Rows: the slope (beta, 1 under parity), the
    t-statistic of beta = 1, and of beta = 0, the number of months.
    """
    universe = results.universes[CURRENCIES_UNIVERSE]
    days = signal_days(universe.index.index, 'M')
    rows = {}
    for currency in universe.excess.columns:
        excess = universe.index[currency].reindex(days)
        gap = -universe.carry[currency].reindex(days)  # dollar rate minus the currency's, per year
        months = horizon / 12.0
        # change of the exchange rate: the excess return of the deposit minus the interest gap it earned
        spot_change = np.log(excess.shift(-horizon) / excess) - (-gap) * months
        pair = pd.concat([gap * months, spot_change], axis=1, keys=['x', 'y']).dropna()
        result = _ols(pair['y'].to_numpy(), pair['x'].to_numpy(), cov_type='HAC', cov_kwds={'maxlags': NW_LAGS})
        if result.n_obs:
            se = result.slope / result.t_stat if result.t_stat else np.nan
            rows[currency] = {
                'beta': result.slope,
                't_beta_1': (result.slope - 1.0) / se,
                't_beta_0': result.t_stat,
                'months': result.n_obs,
            }
    return pd.DataFrame(rows).T
