import logging
import math
from enum import Enum
from typing import NamedTuple

import numpy as np
import pandas as pd
import statsmodels.api as sm

from portfolio_manager.backtest.portfolio import DEFAULT_FREQ_HIST_DATA, PortfolioBacktestData
from portfolio_manager.config.settings import DEFAULT_CORR_DATA_FREQ, RESULTS_DIR
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


def poly_reg(y: pd.Series, x: pd.Series, degree: int = 2) -> (tuple[float], tuple[float], float):
    df = pd.concat([y, x], axis=1).dropna()
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    y = df.iloc[:, 0].values
    _x = df.iloc[:, 1].values
    x = np.ones_like(_x)
    for n in range(degree):
        x = np.column_stack((x, np.power(_x, n + 1).T))
    model = sm.OLS(y, x).fit()
    coeffs = model.params
    pvalues = model.pvalues
    r2 = model.rsquared
    return coeffs, pvalues, r2


def compute_pa_return_last_n_years(nav: pd.Series, freq: str, n_years: int = None) -> float:
    num_periods = n_years * ANN_FACTOR_DICT[freq]
    return (1.0 + nav.resample(freq).last().ffill().pct_change(num_periods).iloc[-1]) ** (1.0 / n_years) - 1


def compute_portfolio_metrics(
    nav: pd.Series | None = None,
    hist_portfolio_data: PortfolioBacktestData | None = None,
    strategy_benchmark: pd.Series | None = None,
    compute_hist_metrics: bool = True,
    print_results: bool = True,
) -> dict[str, PD_DATA_TYPES]:
    if hist_portfolio_data is not None:
        nav = hist_portfolio_data.nav_eff

    # remove initial nans from NAV
    first_idx = nav.first_valid_index()
    nav = nav.loc[first_idx:]

    initial_cash_pos = nav.iloc[0]
    # detect the sampling frequency
    if hist_portfolio_data is None:
        freq = pd.infer_freq(nav.index)
        logger.debug('Inferred sampling frequency: %s', freq)
    elif hist_portfolio_data.freq is None:
        freq = pd.infer_freq(nav.index)
        logger.debug('Inferred sampling frequency: %s', freq)
    else:
        freq = hist_portfolio_data.freq
        logger.debug('Using input sampling frequency: %s', freq)
    if freq is None:
        freq = DEFAULT_FREQ_HIST_DATA
        logger.debug('Switching to default sampling frequency: %s', freq)

    total_return_all_samples = nav.iloc[-1] / initial_cash_pos - 1
    prices_eoy = nav.resample('Y').last()
    if nav.index[0] not in prices_eoy.index:
        prices_eoy = pd.concat([nav.iloc[[0]], prices_eoy])
    returns_yearly = prices_eoy.pct_change().dropna().rename('return')
    returns_yearly.index = returns_yearly.index.year
    returns_yearly.index.name = 'Year'
    returns_yearly['Total'] = total_return_all_samples
    returns_yearly = returns_yearly.reindex(index=returns_yearly.index[::-1])
    returns_monthly = nav.resample('M').last().pct_change().dropna().rename('return')
    return_1y = nav.resample(freq).last().ffill().pct_change(ANN_FACTOR_DICT[freq]).iloc[-1]
    return_3y_ann = compute_pa_return_last_n_years(nav=nav, freq=freq, n_years=3)
    return_5y_ann = compute_pa_return_last_n_years(nav=nav, freq=freq, n_years=5)

    """ calculate portfolio alpha and beta """
    if strategy_benchmark is not None:
        alpha, beta, pval_alpha = regress_strat_vs_bm(nav, strategy_benchmark)
        _, beta_neg, _ = regress_strat_vs_bm(nav, strategy_benchmark, return_sign='neg')
        _, beta_pos, _ = regress_strat_vs_bm(nav, strategy_benchmark, return_sign='pos')
    else:
        alpha, beta, pval_alpha, beta_neg, beta_pos = np.nan, np.nan, np.nan, np.nan, np.nan

    """ calculate risk metrics """

    # calculate per-annum return
    def compute_total_return(nav: pd.Series) -> float:
        return nav.iloc[-1] / nav.iloc[0] - 1

    def compute_n_years(nav: pd.Series) -> float:
        return (len(nav.resample(freq).last()) - 1) / ANN_FACTOR_DICT[freq]

    def compute_pa_return(nav: pd.Series) -> float:
        n_years = compute_n_years(nav)
        total_return = compute_total_return(nav)
        return (1.0 + total_return) ** (1.0 / n_years) - 1

    total_return = compute_total_return(nav)
    pa_return = compute_pa_return(nav)
    # calculate the portfolio standard deviation (volatility)
    returns_resampled = nav.resample(freq).last().ffill().pct_change()
    volatility = math.sqrt(ANN_FACTOR_DICT[freq]) * returns_resampled.std()
    # calculate sharpe ratio
    risk_free_rate = 0.0
    sharpe_ratio = (pa_return - risk_free_rate) / volatility
    # sortino ratio
    volatility_downside = math.sqrt(ANN_FACTOR_DICT[freq]) * returns_resampled[returns_resampled < 0].std()
    sortino_ratio = (pa_return - risk_free_rate) / volatility_downside
    # calculate skewness
    portfolios_skew = returns_resampled.skew()
    # calculate max drawdown (maxdd)
    nav_cummax = nav.cummax()
    max_dd = (nav.subtract(nav_cummax).div(nav_cummax)).abs().max()
    # turnover
    if hist_portfolio_data is not None:
        turnover = (
            hist_portfolio_data.transaction_value.sum(axis=1)
            .iloc[1:]
            .abs()
            .div(hist_portfolio_data.nav.iloc[1:])
            .rename(PerfDataTabs.TURNOVER)
        )
        turnover_mean_daily = turnover.resample(freq).sum().mean() / 365 * ANN_FACTOR_DICT[freq]
    else:
        turnover = pd.Series()
        turnover_mean_daily = np.nan
    # historical performance metrics
    if compute_hist_metrics:
        min_periods = MIN_PERIODS_DICT[freq]
        pa_return_hist = (
            nav.rolling(len(nav), min_periods=min_periods).apply(compute_pa_return).rename(Metrics.PA_RETURN.name)
        )
        volatility_hist = (
            math.sqrt(ANN_FACTOR_DICT[freq]) * returns_resampled.rolling(len(nav), min_periods=min_periods).std()
        )
        volatility_hist = volatility_hist.rename(Metrics.VOLATILITY.name)
        sharpe_ratio_hist = ((pa_return_hist - risk_free_rate) / volatility_hist).rename(Metrics.SHARPE_RATIO.name)

        def compute_downside_volatility(returns: pd.Series) -> float:
            return returns[returns < 0].std()

        down_vol_hist = math.sqrt(ANN_FACTOR_DICT[freq]) * returns_resampled.rolling(
            len(nav), min_periods=min_periods
        ).apply(compute_downside_volatility)
        sortino_ratio_hist = ((pa_return_hist - risk_free_rate) / down_vol_hist).rename(Metrics.SORTINO_RATIO.name)

    # put all together in a dictionary
    risk_metrics = pd.Series(
        {
            Metrics.TOTAL_RETURN.name: total_return,
            Metrics.PA_RETURN.name: pa_return,
            Metrics.LAST_YEAR_RETURN.name: return_1y,
            Metrics.ANN_3Y_RETURN.name: return_3y_ann,
            Metrics.ANN_5Y_RETURN.name: return_5y_ann,
            Metrics.VOLATILITY.name: volatility,
            Metrics.SHARPE_RATIO.name: sharpe_ratio,
            Metrics.SORTINO_RATIO.name: sortino_ratio,
            Metrics.BEST_MONTH.name: returns_monthly.max(),
            Metrics.WORST_MONTH.name: returns_monthly.min(),
            Metrics.MAX_DD.name: max_dd,
            Metrics.BETA_OVERALL.name: beta,
            Metrics.BETA_UP_MONTH.name: beta_pos,
            Metrics.BETA_DOWN_MONTH.name: beta_neg,
            Metrics.SKEWNESS.name: portfolios_skew,
            Metrics.TURNOVER.name: turnover_mean_daily,
            Metrics.ALPHA.name: alpha,
            Metrics.BETA.name: beta,
            Metrics.PVAL_ALPHA.name: pval_alpha,
        },
        name=nav.name,
    )
    if print_results:
        logger.info('Risk metrics of %s:\n%s', nav.name, risk_metrics)
    results_dict = {
        PerfDataTabs.RETURNS_YEARLY: returns_yearly,
        PerfDataTabs.RETURNS_MONTHLY: returns_monthly,
        PerfDataTabs.RISK_METRICS: risk_metrics,
        PerfDataTabs.TURNOVER: turnover,
    }
    if compute_hist_metrics:
        results_dict[PerfDataTabs.HIST_PERF_METRICS] = pd.concat(
            [pa_return_hist, volatility_hist, sharpe_ratio_hist, sortino_ratio_hist], axis=1
        )
    # correlation of the weekly returns of adjusted closing prices (as shown in the dashboard)
    if hist_portfolio_data is not None:
        if hist_portfolio_data.close_adj is not None:
            returns_weekly = hist_portfolio_data.close_adj.resample(DEFAULT_CORR_DATA_FREQ).last().pct_change()
            results_dict[PerfDataTabs.CORRELATION] = returns_weekly.corr()
    return results_dict


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
