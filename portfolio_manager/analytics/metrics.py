import logging
import math
from enum import Enum
from typing import NamedTuple

import numpy as np
import pandas as pd
import statsmodels.api as sm

from portfolio_manager.backtest.portfolio import DEFAULT_FREQ_HIST_DATA, PortfolioBacktestData
from portfolio_manager.config.settings import DEFAULT_CORR_DATA_FREQ, RESULTS_DIR, RISK_FREE_RATE
from portfolio_manager.storage.files import PD_DATA_TYPES, save_df_dict_to_excel
from portfolio_manager.utils.dates import ANN_FACTOR_DICT

logger = logging.getLogger(__name__)

MIN_PERIODS_DICT = {'H': 180 * 24, 'D': 180, 'B': 130, 'W': 26, '2W': 13, 'M': 6, '2M': 3, 'Q': 2, '2Q': 2, 'Y': 2}


class Metric(NamedTuple):
    name: str
    format: str = '{:.2%}'
    sort: str | None = None

    def to_ag_grid_format_func(self):
        return (
            f'params.value ? '
            f"d3.format('{self.format.replace(':', '').replace('{', '').replace('}', '')}')(params.value) "
            f": ''"
        )


class Metrics(Metric, Enum):
    TOTAL_RETURN = Metric('Total Return')
    PA_RETURN = Metric('P.a. Return')
    LAST_YEAR_RETURN = Metric('1Y Return')
    ANN_3Y_RETURN = Metric('3Y Return')
    ANN_5Y_RETURN = Metric('5Y Return')
    VOLATILITY = Metric('Volatility')
    SHARPE_RATIO = Metric('Sharpe ratio', format='{:.2f}', sort='desc')
    SORTINO_RATIO = Metric('Sortino ratio', format='{:.2f}')
    BEST_MONTH = Metric('Best month')
    WORST_MONTH = Metric('Worst month')
    MAX_DD = Metric('Max DD')
    BETA_OVERALL = Metric('Overall beta', format='{:.2f}')
    BETA_UP_MONTH = Metric('Up Month Beta', format='{:.2f}')
    BETA_DOWN_MONTH = Metric('Down Month Beta', format='{:.2f}')
    SKEWNESS = Metric('Skewness', format='{:.2f}')
    TURNOVER = Metric('Turnover\n(daily avg.)')
    ALPHA = Metric('alpha')
    BETA = Metric('beta', format='{:.2f}')
    PVAL_ALPHA = Metric('pval alpha')


def compute_pa_return_last_n_years(nav: pd.Series, freq: str, n_years: int = None) -> float:
    num_periods = n_years * ANN_FACTOR_DICT[freq]
    return (1.0 + nav.resample(freq).last().ffill().pct_change(num_periods).iloc[-1]) ** (1.0 / n_years) - 1


def compute_total_return(nav: pd.Series) -> float:
    return nav.iloc[-1] / nav.iloc[0] - 1


def compute_pa_return(nav: pd.Series, freq: str) -> float:
    # annualized return over the whole NAV
    n_years = (len(nav.resample(freq).last()) - 1) / ANN_FACTOR_DICT[freq]
    return (1.0 + compute_total_return(nav)) ** (1.0 / n_years) - 1


def downside_deviation(returns: pd.Series) -> float:
    # root mean square of the negative returns over all periods (positive returns count as 0)
    return math.sqrt(returns.clip(upper=0.0).pow(2).mean())


def compute_portfolio_metrics(
    nav: pd.Series | None = None,
    hist_portfolio_data: PortfolioBacktestData | None = None,
    strategy_benchmark: pd.Series | None = None,
    compute_hist_metrics: bool = True,
    print_results: bool = True,
) -> dict[str, PD_DATA_TYPES]:
    # performance of a NAV (or of the effective NAV of a backtest): returns, risk metrics and their history
    if hist_portfolio_data is not None:
        nav = hist_portfolio_data.nav_eff
    nav = nav.loc[nav.first_valid_index() :]  # remove initial nans
    freq = _sampling_freq(nav, hist_portfolio_data)
    returns_resampled = nav.resample(freq).last().ffill().pct_change()
    returns_monthly = nav.resample('M').last().pct_change().dropna().rename('return')
    turnover, turnover_mean_daily = _turnover(hist_portfolio_data, freq)

    risk_metrics = _risk_metrics(nav, freq, returns_resampled, returns_monthly, strategy_benchmark, turnover_mean_daily)
    if print_results:
        logger.info('Risk metrics of %s:\n%s', nav.name, risk_metrics)
    results_dict = {
        PerfDataTabs.RETURNS_YEARLY: _yearly_returns(nav),
        PerfDataTabs.RETURNS_MONTHLY: returns_monthly,
        PerfDataTabs.RISK_METRICS: risk_metrics,
        PerfDataTabs.TURNOVER: turnover,
    }
    if compute_hist_metrics:
        results_dict[PerfDataTabs.HIST_PERF_METRICS] = _historical_metrics(nav, freq)
    # correlation of the weekly returns of adjusted closing prices (as shown in the dashboard)
    if hist_portfolio_data is not None and hist_portfolio_data.close_adj is not None:
        returns_weekly = hist_portfolio_data.close_adj.resample(DEFAULT_CORR_DATA_FREQ).last().pct_change()
        results_dict[PerfDataTabs.CORRELATION] = returns_weekly.corr()
    return results_dict


def _sampling_freq(nav: pd.Series, hist_portfolio_data: PortfolioBacktestData | None) -> str:
    # frequency of the backtest data if known, otherwise inferred from the NAV (default if that fails)
    if hist_portfolio_data is not None and hist_portfolio_data.freq is not None:
        freq = hist_portfolio_data.freq
        logger.debug('Using input sampling frequency: %s', freq)
    else:
        freq = pd.infer_freq(nav.index)
        logger.debug('Inferred sampling frequency: %s', freq)
    if freq is None:
        freq = DEFAULT_FREQ_HIST_DATA
        logger.debug('Switching to default sampling frequency: %s', freq)
    return freq


def _yearly_returns(nav: pd.Series) -> pd.Series:
    # calendar-year returns (the first year from the first NAV value), latest first, plus the total return
    prices_eoy = nav.resample('Y').last()
    if nav.index[0] not in prices_eoy.index:
        prices_eoy = pd.concat([nav.iloc[[0]], prices_eoy])
    returns_yearly = prices_eoy.pct_change().dropna().rename('return')
    returns_yearly.index = returns_yearly.index.year
    returns_yearly.index.name = 'Year'
    returns_yearly['Total'] = compute_total_return(nav)
    return returns_yearly.reindex(index=returns_yearly.index[::-1])


def _turnover(hist_portfolio_data: PortfolioBacktestData | None, freq: str) -> tuple[pd.Series, float]:
    # daily traded value (purchases and sales, gross) relative to the NAV, and its average (only for backtest data)
    if hist_portfolio_data is None:
        return pd.Series(), np.nan
    turnover = (
        hist_portfolio_data.transaction_value.abs()
        .sum(axis=1)
        .iloc[1:]
        .div(hist_portfolio_data.nav.iloc[1:])
        .rename(PerfDataTabs.TURNOVER)
    )
    return turnover, turnover.resample(freq).sum().mean() / 365 * ANN_FACTOR_DICT[freq]


def _risk_metrics(
    nav: pd.Series,
    freq: str,
    returns_resampled: pd.Series,
    returns_monthly: pd.Series,
    strategy_benchmark: pd.Series | None,
    turnover_mean_daily: float,
) -> pd.Series:
    ann_factor = math.sqrt(ANN_FACTOR_DICT[freq])
    pa_return = compute_pa_return(nav, freq)
    volatility = ann_factor * returns_resampled.std()
    # no losses: no downside deviation, so no Sortino ratio (instead of an infinite one)
    volatility_downside = ann_factor * downside_deviation(returns_resampled) or np.nan
    nav_cummax = nav.cummax()
    if strategy_benchmark is not None:
        alpha, beta, pval_alpha = regress_strat_vs_bm(nav, strategy_benchmark)
        _, beta_neg, _ = regress_strat_vs_bm(nav, strategy_benchmark, return_sign='neg')
        _, beta_pos, _ = regress_strat_vs_bm(nav, strategy_benchmark, return_sign='pos')
    else:
        alpha, beta, pval_alpha, beta_neg, beta_pos = np.nan, np.nan, np.nan, np.nan, np.nan
    return pd.Series(
        {
            Metrics.TOTAL_RETURN.name: compute_total_return(nav),
            Metrics.PA_RETURN.name: pa_return,
            Metrics.LAST_YEAR_RETURN.name: nav.resample(freq).last().ffill().pct_change(ANN_FACTOR_DICT[freq]).iloc[-1],
            Metrics.ANN_3Y_RETURN.name: compute_pa_return_last_n_years(nav=nav, freq=freq, n_years=3),
            Metrics.ANN_5Y_RETURN.name: compute_pa_return_last_n_years(nav=nav, freq=freq, n_years=5),
            Metrics.VOLATILITY.name: volatility,
            Metrics.SHARPE_RATIO.name: (pa_return - RISK_FREE_RATE) / volatility,
            Metrics.SORTINO_RATIO.name: (pa_return - RISK_FREE_RATE) / volatility_downside,
            Metrics.BEST_MONTH.name: returns_monthly.max(),
            Metrics.WORST_MONTH.name: returns_monthly.min(),
            Metrics.MAX_DD.name: (nav.subtract(nav_cummax).div(nav_cummax)).abs().max(),
            Metrics.BETA_OVERALL.name: beta,
            Metrics.BETA_UP_MONTH.name: beta_pos,
            Metrics.BETA_DOWN_MONTH.name: beta_neg,
            Metrics.SKEWNESS.name: returns_resampled.skew(),
            Metrics.TURNOVER.name: turnover_mean_daily,
            Metrics.ALPHA.name: alpha,
            Metrics.BETA.name: beta,
            Metrics.PVAL_ALPHA.name: pval_alpha,
        },
        name=nav.name,
    )


def _historical_metrics(nav: pd.Series, freq: str) -> pd.DataFrame:
    # return, volatility, Sharpe and Sortino ratios at each period of freq, from the start of the NAV up to that
    # period; all computed on the NAV resampled at freq, so that the minimum history is a number of periods
    names = [m.name for m in (Metrics.PA_RETURN, Metrics.VOLATILITY, Metrics.SHARPE_RATIO, Metrics.SORTINO_RATIO)]
    nav_resampled = nav.resample(freq).last().ffill()
    min_periods = MIN_PERIODS_DICT[freq]
    if len(nav_resampled) < min_periods:
        # young portfolio: not enough history for any value
        return pd.DataFrame(np.nan, index=nav_resampled.index, columns=names)
    returns = nav_resampled.pct_change()
    ann_factor = math.sqrt(ANN_FACTOR_DICT[freq])
    # annualized return from the start (as compute_pa_return): k periods elapsed after the k-th period
    periods_elapsed = np.arange(len(nav_resampled), dtype=float)
    with np.errstate(divide='ignore'):
        exponent = ANN_FACTOR_DICT[freq] / periods_elapsed
    pa_return = (nav_resampled / nav_resampled.iloc[0]) ** exponent - 1
    pa_return = pa_return.where(periods_elapsed + 1 >= min_periods).rename(Metrics.PA_RETURN.name)
    volatility = (ann_factor * returns.expanding(min_periods=min_periods).std()).rename(Metrics.VOLATILITY.name)
    # downside deviation up to each period (as downside_deviation)
    down_volatility = ann_factor * returns.clip(upper=0.0).pow(2).expanding(min_periods=min_periods).mean().pow(0.5)
    down_volatility = down_volatility.replace(0.0, np.nan)
    sharpe_ratio = ((pa_return - RISK_FREE_RATE) / volatility).rename(Metrics.SHARPE_RATIO.name)
    sortino_ratio = ((pa_return - RISK_FREE_RATE) / down_volatility).rename(Metrics.SORTINO_RATIO.name)
    return pd.concat([pa_return, volatility, sharpe_ratio, sortino_ratio], axis=1)


def to_str_risk_metrics(risk_metrics: PD_DATA_TYPES):
    if isinstance(risk_metrics, pd.DataFrame):
        risk_metrics = risk_metrics.iloc[:, 0]
    risk_metrics_str = pd.Series(name='Parameter', dtype=str)
    for metric in Metrics:
        if metric.name not in risk_metrics:
            continue
        if np.isnan(risk_metrics[metric.name]):
            continue
        risk_metrics_str[metric.name] = metric.format.format(risk_metrics[metric.name])
    return risk_metrics_str.copy()


def regress_strat_vs_bm(nav: pd.Series, strategy_benchmark: pd.Series, freq: str = 'M', return_sign: str = 'all'):
    try:
        bm = strategy_benchmark.copy()
        df = pd.concat([nav, bm], axis=1).resample(freq).last().pct_change().dropna()
        if return_sign == 'neg':
            df = df[df.iloc[:, 1] < 0]
        elif return_sign == 'pos':
            df = df[df.iloc[:, 1] > 0]
        y = df.iloc[:, 0]
        x = df.iloc[:, 1]
        x = sm.add_constant(x)
        model = sm.OLS(y, x).fit()
        alpha = ANN_FACTOR_DICT[freq] * model.params[0]
        beta = model.params[1]
        pval_alpha = model.pvalues[0]
    except (ValueError, IndexError):
        alpha = np.nan
        beta = np.nan
        pval_alpha = np.nan
    return alpha, beta, pval_alpha


def compute_results_from_navs(
    navs: pd.Series | pd.DataFrame,
    nav_benchmark: pd.Series | None = None,
    file_name: str | None = None,
    save: bool = True,
) -> dict[str, pd.DataFrame]:
    if isinstance(navs, pd.Series):
        navs = navs.to_frame()
    results_dict = {
        PerfDataTabs.RETURNS_YEARLY: pd.DataFrame(),
        PerfDataTabs.RETURNS_MONTHLY: pd.DataFrame(),
        PerfDataTabs.RISK_METRICS: pd.DataFrame(),
    }
    for col in navs.columns.to_list():
        nav = navs[col].copy()
        results_single = compute_portfolio_metrics(nav=nav, strategy_benchmark=nav_benchmark)
        for label in results_dict:
            logger.debug('%s of %s:\n%s', label, nav.name, results_single[label])
            results_dict[label] = pd.concat([results_dict[label], results_single[label].rename(nav.name)], axis=1)
    results_dict[PerfDataTabs.CORRELATION] = results_dict[PerfDataTabs.RETURNS_MONTHLY].corr()
    if save:
        if file_name is None:
            file_name = 'results'
        save_df_dict_to_excel(df_dict=results_dict, folder_name=RESULTS_DIR, file_name=file_name)
    return results_dict


class PerfDataTabs:
    RETURNS_YEARLY = 'returns_yearly'
    RETURNS_MONTHLY = 'returns_monthly'
    RISK_METRICS = 'risk_metrics'
    HIST_PERF_METRICS = 'hist_perf_metrics'
    TURNOVER = 'turnover'
    NAV = 'NAV'
    UNITS = 'units'
    WEIGHTS = 'weights'
    TARGET_EXP = 'target_exposure'
    SIGNALS_SELEC = 'signals_selection'
    SIGNALS_ALLOC = 'signals_allocation'
    PRICES = 'prices'
    CUM_PNL = 'cum_pnl'
    CORRELATION = 'correlation'
