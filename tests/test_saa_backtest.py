import numpy as np
import pandas as pd
import pytest

import portfolio_manager.backtest.saa as saa
import portfolio_manager.storage.queries as sql
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.config.account_settings import Benchmark, BenchmarkComponent
from portfolio_manager.config.accounts import Account, Brokers
from portfolio_manager.config.allocations import Allocation, AllocationInstrument, Bucket
from portfolio_manager.optimization.settings import AllocationStrats

CHF = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')
DAYS = pd.bdate_range('2022-01-03', '2024-12-31')
LATE = pd.Timestamp('2022-07-01')  # the bond fund starts trading later


def store(ticker: str, drift: float, vol: float, start: pd.Timestamp = DAYS[0]) -> None:
    days = DAYS[DAYS >= start]
    rng = np.random.default_rng(sum(ticker.encode()))  # the same prices at every run
    closes = 100.0 * np.exp(np.cumsum(rng.normal(drift, vol, len(days))))
    instrument_id = sql.upsert_yahoo_instrument(ticker, {'currency': 'CHF', 'name_long': ticker})
    sql.save_yahoo_prices(instrument_id, pd.DataFrame({'close': closes, 'volume': 1000.0}, index=days))


@pytest.fixture
def prices(temp_db):
    store('EQ1.SW', 0.0004, 0.012)
    store('EQ2.SW', 0.0003, 0.010)
    store('BD1.SW', 0.0001, 0.003, start=LATE)


def allocation(weight_mode: str = 'manual') -> Allocation:
    # 70% equity split by region, 30% bonds
    return Allocation(
        name='Test',
        class_targets={'Equity': 0.7, 'Fixed Income': 0.3},
        splits={'Equity': 'region'},
        buckets=[Bucket('Equity', 'Switzerland', 0.5), Bucket('Equity', 'Global', 0.5)],
        instruments=[
            AllocationInstrument('EQ1.SW', None, 'Equity', 'Switzerland', 1.0),
            AllocationInstrument('EQ2.SW', None, 'Equity', 'Global', 1.0),
            AllocationInstrument('BD1.SW', None, 'Fixed Income', None, 1.0),
        ],
        weight_mode=weight_mode,
        rebalancing_freq='Q',
    )


def test_manual_weights_rebalanced_from_the_first_common_day(prices):
    result = saa.backtest_allocation(CHF, allocation(), save=False)
    nav = result.backtest.nav
    assert nav.index[0] == LATE  # the first day all instruments have prices
    assert result.weights.iloc[0].to_dict() == pytest.approx({'EQ1.SW': 0.35, 'EQ2.SW': 0.35, 'BD1.SW': 0.3})
    weights = result.backtest.effective_weights
    np.testing.assert_allclose(weights.iloc[0][['EQ1.SW', 'EQ2.SW', 'BD1.SW']], [0.35, 0.35, 0.3], atol=1e-3)
    # the weights drift, and are brought back to the targets on the last trading day of each quarter
    trades = result.backtest.transaction_value.abs().sum(axis=1)
    traded_days = trades[trades > 0].index
    quarter_ends = nav.index.to_series().resample('Q').last()
    assert set(traded_days[1:]) <= set(quarter_ends)  # the first day buys, then only quarter ends
    for day in traded_days[1:4]:  # positions within 1 point of their target are not traded (engine max_target_dev)
        np.testing.assert_allclose(weights.loc[day, ['EQ1.SW', 'EQ2.SW', 'BD1.SW']], [0.35, 0.35, 0.3], atol=0.01)


def test_optimized_weights_within_the_class_ranges(prices):
    portfolio = allocation('optimize')
    portfolio.settings.method = AllocationStrats.MIN_VAR
    portfolio.settings.class_tolerance = 0.05
    portfolio.settings.optimization_freq = 'Q'
    result = saa.backtest_allocation(CHF, portfolio, start=pd.Timestamp('2023-01-02'), save=False)
    assert not result.failed_dates and result.backtest.nav.index[0] == pd.Timestamp('2023-01-02')
    weights = result.weights.dropna()
    np.testing.assert_allclose(weights.sum(axis=1), 1.0, atol=1e-6)  # fully invested
    equity = weights['EQ1.SW'] + weights['EQ2.SW']
    assert equity.between(0.65 - 1e-6, 0.75 + 1e-6).all() and weights['BD1.SW'].between(0.25 - 1e-6, 0.35 + 1e-6).all()


def test_instrument_without_prices_cannot_be_backtested(prices):
    portfolio = allocation()
    portfolio.instruments[2] = AllocationInstrument('NOPE.SW', None, 'Fixed Income', None, 1.0)
    with pytest.raises(ValueError, match='No stored prices for NOPE.SW'):
        saa.backtest_allocation(CHF, portfolio, save=False)


def test_backtest_saved_under_the_name_of_the_allocation(prices, monkeypatch):
    saved = []
    monkeypatch.setattr(saa, 'save_backtest_data', lambda hist_portfolio_data: saved.append(hist_portfolio_data))
    saa.backtest_allocation(CHF, allocation())
    assert [b.name for b in saved] == ['Portfolio CHF SAA Test']


def test_comparison_with_the_benchmark_and_the_portfolio(prices, monkeypatch):
    index = pd.bdate_range('2023-01-02', '2023-06-30')
    benchmark = Benchmark([BenchmarkComponent('A', 'A', 0.5), BenchmarkComponent('B', 'B', 0.5)], 'M')
    monkeypatch.setattr(saa, 'account_benchmark', lambda account: benchmark)
    benchmark_prices = pd.DataFrame(
        {'A': np.linspace(100, 110, 400), 'B': 50.0}, index=pd.bdate_range('2022-06-01', periods=400)
    )
    monkeypatch.setattr(saa, 'get_benchmark_adj_prices', lambda account, b: benchmark_prices)
    portfolio_nav = pd.Series(np.linspace(200, 220, 60), index=pd.bdate_range('2023-04-03', periods=60))
    monkeypatch.setattr(
        saa, 'load_backtest_data', lambda name, fields: PortfolioBacktestData(name=name, nav_eff=portfolio_nav)
    )
    navs = saa.comparison_navs(CHF, index)
    assert list(navs.columns) == [saa.BENCHMARK_LABEL, saa.PORTFOLIO_LABEL]
    assert navs[saa.BENCHMARK_LABEL].iloc[0] == pytest.approx(100.0) and navs[saa.BENCHMARK_LABEL].iloc[-1] > 100.0
    # the portfolio from its first day within the dates, also at 100
    portfolio = navs[saa.PORTFOLIO_LABEL]
    assert portfolio.loc[:'2023-03-31'].isna().all() and portfolio.dropna().iloc[0] == pytest.approx(100.0)


def test_optimized_weights_from_the_first_day_with_enough_history(prices):
    # without a start: once every instrument has 3 months of history, invested from the first day
    portfolio = allocation('optimize')
    portfolio.settings.optimization_freq = 'Q'
    result = saa.backtest_allocation(CHF, portfolio, save=False)
    start = result.backtest.nav.index[0]
    assert start == pd.Timestamp('2022-10-03')  # 3 months after LATE (the first business day)
    assert result.weights.index[0] == start and not result.failed_dates
    # invested from the first day (0.1% kept in cash for the transaction costs), not waiting for the first quarter end
    assert result.backtest.effective_weights['Cash'].iloc[0] < 0.002
