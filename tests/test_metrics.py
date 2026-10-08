import numpy as np
import pandas as pd
import pytest

from portfolio_manager.analytics.metrics import Metrics, PerfDataTabs, compute_portfolio_metrics


def metrics(nav, **kwargs) -> pd.Series:
    return compute_portfolio_metrics(nav=nav, print_results=False, **kwargs)[PerfDataTabs.RISK_METRICS]


def test_annualized_return_of_a_nav_doubling_in_two_years():
    index = pd.bdate_range('2020-01-01', periods=2 * 261 + 1)  # 261 business days a year
    nav = pd.Series(100.0 * 2.0 ** (np.arange(len(index)) / 522), index=index, name='NAV')
    risk = metrics(nav)
    assert risk[Metrics.TOTAL_RETURN.name] == pytest.approx(1.0)
    assert risk[Metrics.PA_RETURN.name] == pytest.approx(np.sqrt(2.0) - 1.0)
    assert risk[Metrics.MAX_DD.name] == pytest.approx(0.0)


def test_max_drawdown_and_best_worst_month():
    index = pd.bdate_range('2021-01-01', '2021-04-30')
    values = np.interp(np.arange(len(index)), [0, 20, 40, len(index) - 1], [100.0, 120.0, 90.0, 130.0])
    risk = metrics(pd.Series(values, index=index, name='NAV'))
    assert risk[Metrics.MAX_DD.name] == pytest.approx(0.25)  # from 120 down to 90
    assert risk[Metrics.WORST_MONTH.name] < 0 < risk[Metrics.BEST_MONTH.name]


def test_yearly_returns_latest_first_with_total():
    index = pd.bdate_range('2019-06-03', '2021-12-31')
    nav = pd.Series(np.linspace(100.0, 160.0, len(index)), index=index, name='NAV')
    yearly = compute_portfolio_metrics(nav=nav, print_results=False)[PerfDataTabs.RETURNS_YEARLY]
    assert yearly.index.tolist() == ['Total', 2021, 2020, 2019]
    assert yearly['Total'] == pytest.approx(0.6)
    assert (1 + yearly.drop('Total')).prod() == pytest.approx(1.6)


def test_strategy_equal_to_benchmark_has_beta_one_and_no_alpha(prices):
    nav = prices['A2'].rename('NAV')
    risk = metrics(nav, strategy_benchmark=prices['A2'].rename('BM'))
    assert risk[Metrics.BETA.name] == pytest.approx(1.0)
    assert risk[Metrics.ALPHA.name] == pytest.approx(0.0, abs=1e-12)


def test_historical_metrics_use_all_data_up_to_each_date(prices):
    nav = prices['A1'].rename('NAV')
    hist = compute_portfolio_metrics(nav=nav, print_results=False)[PerfDataTabs.HIST_PERF_METRICS]
    assert list(hist.columns) == [
        m.name for m in (Metrics.PA_RETURN, Metrics.VOLATILITY, Metrics.SHARPE_RATIO, Metrics.SORTINO_RATIO)
    ]
    # the last value equals the metric over the whole NAV
    risk = metrics(nav)
    assert hist[Metrics.PA_RETURN.name].iloc[-1] == pytest.approx(risk[Metrics.PA_RETURN.name])
    assert hist[Metrics.VOLATILITY.name].iloc[-1] == pytest.approx(risk[Metrics.VOLATILITY.name])


def test_young_portfolio_has_empty_historical_metrics():
    # less than the minimum history of the historical metrics (130 business days)
    index = pd.bdate_range('2021-01-01', periods=60)
    nav = pd.Series(np.linspace(100.0, 105.0, len(index)), index=index, name='NAV')
    results = compute_portfolio_metrics(nav=nav, print_results=False)
    hist = results[PerfDataTabs.HIST_PERF_METRICS]
    assert hist.index.equals(nav.index) and hist.isna().all().all()
    assert results[PerfDataTabs.RISK_METRICS][Metrics.TOTAL_RETURN.name] == pytest.approx(0.05)
