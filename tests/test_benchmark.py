import pytest

import portfolio_manager.backtest.workflows as workflows
from portfolio_manager.config.account_settings import DEFAULT_BENCHMARKS, Benchmark, BenchmarkComponent
from portfolio_manager.config.accounts import Account, Brokers

CHF = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')


@pytest.fixture
def benchmark_prices(prices, monkeypatch):
    # saved benchmark of two instruments
    benchmark = Benchmark([BenchmarkComponent('A0', 'XX0000000000', 0.7), BenchmarkComponent('A2', 'A2.L', 0.3)], 'Q')
    searched = []
    monkeypatch.setattr(workflows, 'query_benchmark', lambda name: benchmark)
    monkeypatch.setattr(
        workflows,
        'get_benchmark_adj_prices',
        lambda account, benchmark: searched.append(benchmark) or prices[['A0', 'A2']],
    )
    monkeypatch.setattr(workflows, 'save_backtest_data', lambda hist_portfolio_data: None)
    return searched


def test_benchmark_backtest_uses_the_saved_benchmark(benchmark_prices, prices):
    data = workflows.backtest_portfolio_benchmark(CHF, index=prices.index[:300])
    assert [c.label for c in benchmark_prices[0].components] == ['A0', 'A2']
    weights = data.effective_weights
    assert list(weights.columns[:2]) == ['A0', 'A2']
    # rebalanced to the weights at the end of each quarter
    assert weights.loc['2018-03-30', ['A0', 'A2']].tolist() == pytest.approx([0.7, 0.3], abs=0.01)


def test_default_benchmark_of_the_currency_without_saved_settings(monkeypatch):
    monkeypatch.setattr(workflows, 'query_benchmark', lambda name: None)
    assert workflows.account_benchmark(CHF) == DEFAULT_BENCHMARKS['CHF']
    with pytest.raises(ValueError, match='no benchmark'):
        workflows.account_benchmark(Account('Portfolio USD', Brokers.DEGIRO, 'usd.json', 'USD'))
