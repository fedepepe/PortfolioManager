import pytest

import portfolio_manager.backtest.workflows as workflows
from portfolio_manager.config.account_settings import Benchmark, BenchmarkComponent
from portfolio_manager.config.accounts import Accounts


@pytest.fixture
def benchmark_prices(prices, monkeypatch):
    # saved benchmark of two instruments; the download returns them in another order, plus an extra listing
    benchmark = Benchmark([BenchmarkComponent('A0', 'XX0000000000', 0.7), BenchmarkComponent('A2', 'A2.L', 0.3)], 'Q')
    searched = []
    monkeypatch.setattr(workflows, 'query_benchmark', lambda name: benchmark)
    monkeypatch.setattr(
        workflows,
        'fetch_instr_adj_prices',
        lambda account, isin_lst, tick_lst: searched.append((isin_lst, tick_lst)) or prices[['A2', 'A1', 'A0']],
    )
    monkeypatch.setattr(workflows, 'save_backtest_data', lambda hist_portfolio_data: None)
    return searched


def test_benchmark_backtest_uses_the_saved_benchmark(benchmark_prices, prices):
    data = workflows.backtest_portfolio_benchmark(Accounts.DEGIRO_CHF, index=prices.index[:300])
    assert benchmark_prices == [(['XX0000000000', 'A2.L'], ['A0', 'A2'])]
    weights = data.effective_weights
    assert list(weights.columns[:2]) == ['A0', 'A2']
    # rebalanced to the weights at the end of each quarter
    assert weights.loc['2018-03-30', ['A0', 'A2']].tolist() == pytest.approx([0.7, 0.3], abs=0.01)


def test_default_benchmark_without_saved_settings(monkeypatch):
    monkeypatch.setattr(workflows, 'query_benchmark', lambda name: None)
    assert workflows.account_benchmark(Accounts.DEGIRO_EUR) == Accounts.DEGIRO_EUR.default_benchmark
