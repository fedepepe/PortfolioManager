import numpy as np
import pandas as pd
import pytest

from portfolio_manager.analytics.metrics import Metrics, PerfDataTabs, compute_portfolio_metrics, downside_deviation
from portfolio_manager.backtest.portfolio import PortfolioBacktestData


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
    assert hist[Metrics.SORTINO_RATIO.name].iloc[-1] == pytest.approx(risk[Metrics.SORTINO_RATIO.name])


def test_young_portfolio_has_empty_historical_metrics():
    # less than the minimum history of the historical metrics (130 business days)
    index = pd.bdate_range('2021-01-01', periods=60)
    nav = pd.Series(np.linspace(100.0, 105.0, len(index)), index=index, name='NAV')
    results = compute_portfolio_metrics(nav=nav, print_results=False)
    hist = results[PerfDataTabs.HIST_PERF_METRICS]
    assert hist.index.equals(nav.index) and hist.isna().all().all()
    assert results[PerfDataTabs.RISK_METRICS][Metrics.TOTAL_RETURN.name] == pytest.approx(0.05)


def test_minimum_history_counts_periods_not_rows():
    from portfolio_manager.analytics.metrics import _historical_metrics

    # one value every other business day: the history is counted in business days (130 needed), not in values
    def every_other_day(n_business_days):
        index = pd.bdate_range('2021-01-01', periods=n_business_days)[::2]
        return pd.Series(np.linspace(100.0, 110.0, len(index)), index=index, name='NAV')

    young = _historical_metrics(every_other_day(120), 'B')  # 60 values, 119 business days
    assert young.isna().all().all()
    hist = _historical_metrics(every_other_day(200), 'B')  # 100 values, 199 business days
    assert len(hist) == 199 and hist[Metrics.VOLATILITY.name].notna().any()


def test_downside_deviation_counts_positive_returns_as_zero():
    returns = pd.Series([0.02, -0.01, 0.03, -0.03])
    assert downside_deviation(returns) == pytest.approx(np.sqrt((0.01**2 + 0.03**2) / 4))


def test_turnover_counts_purchases_and_sales():
    index = pd.bdate_range('2021-01-01', periods=300)
    nav = pd.Series(np.linspace(1000.0, 1100.0, len(index)), index=index, name='NAV')
    trades = pd.DataFrame(0.0, index=index, columns=['A', 'B'])
    trades.iloc[100] = [-50.0, 50.0]  # rebalancing: buy A for 50, sell B for 50 (net zero)
    data = PortfolioBacktestData(name='x', nav=nav, units=trades, nav_eff=nav, transaction_value=trades, freq='B')
    turnover = compute_portfolio_metrics(hist_portfolio_data=data, print_results=False)[PerfDataTabs.TURNOVER]
    assert turnover.iloc[99] == pytest.approx(100.0 / nav.iloc[100])  # day 100 (the first day is left out)
