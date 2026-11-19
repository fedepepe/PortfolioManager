import numpy as np
import pandas as pd
import pytest

import portfolio_manager.storage.queries as sql
from portfolio_manager.research import signals
from portfolio_manager.research.backtest import (
    budgets,
    cross_section_weights,
    run_backtest,
    signal_days,
    summary,
    time_series_weights,
)
from portfolio_manager.research.currencies import CurrencyIndices
from portfolio_manager.research.returns import ResearchData
from portfolio_manager.research.strategies import (
    CURRENCIES_UNIVERSE,
    HEDGING_PREFIX,
    Strategy,
    build_universes,
    default_strategies,
    run_strategies,
)

DAYS = pd.bdate_range('2024-01-01', periods=10)


def test_signal_days_are_the_last_business_day_of_each_period():
    days = pd.bdate_range('2024-01-01', '2024-03-31').delete([-1])  # 29 March missing (a holiday)
    assert list(signal_days(days, 'M')) == list(pd.to_datetime(['2024-01-31', '2024-02-29', '2024-03-28']))
    assert signal_days(days, 'W-FRI')[0] == pd.Timestamp('2024-01-05')


def test_weights_drift_with_the_returns_until_the_next_trade():
    excess = pd.DataFrame({'A': 0.0, 'B': 0.0}, index=DAYS)
    excess.loc[DAYS[2], 'A'] = 0.10  # A gains 10% on the third day
    weights = pd.DataFrame({'A': [0.5, 0.5], 'B': [0.5, 0.5]}, index=[DAYS[0], DAYS[4]])
    result = run_backtest(weights, excess, cost_rate=0.0)
    # traded at the close of the next day (lag 1): returns from the third day
    assert result.returns.index[0] == DAYS[1]
    assert result.returns[DAYS[2]] == pytest.approx(0.05)
    # at the second trade A weighs 0.55 / 1.05: back to 0.5, traded both ways
    assert result.turnover.iloc[1] == pytest.approx(2 * (0.55 / 1.05 - 0.5))
    assert result.nav().iloc[-1] == pytest.approx(1.05)


def test_trades_pay_their_cost_and_shorts_earn_the_opposite():
    excess = pd.DataFrame({'A': 0.01}, index=DAYS)
    long = run_backtest(pd.DataFrame({'A': [1.0]}, index=[DAYS[0]]), excess, cost_rate=0.001)
    assert long.returns.iloc[0] == pytest.approx(-0.001)  # buying the whole portfolio
    assert long.returns.iloc[1] == pytest.approx(0.01)
    short = run_backtest(pd.DataFrame({'A': [-1.0]}, index=[DAYS[0]]), excess, cost_rate=0.0)
    assert short.returns.iloc[1] == pytest.approx(-0.01)
    assert summary(long)['turnover'] == pytest.approx(1.0 / (len(long.returns) / 261))


def test_time_series_and_cross_section_weights():
    score = pd.DataFrame({'A': [0.2], 'B': [-0.1], 'C': [0.05], 'D': [np.nan]})
    budget = budgets(score.notna())
    assert time_series_weights(score, budget).iloc[0].tolist() == pytest.approx([1 / 3, 0.0, 1 / 3, 0.0])
    assert time_series_weights(score, budget, long_short=True).iloc[0].tolist() == pytest.approx(
        [1 / 3, -1 / 3, 1 / 3, 0.0]
    )
    # the top third of 3 instruments: the best one, fully invested; short the worst one
    assert cross_section_weights(score, budget, long_short=True).iloc[0].tolist() == [1.0, -1.0, 0.0, 0.0]
    assert (cross_section_weights(score.iloc[:, :2], budgets(score.iloc[:, :2].notna())) == 0).all().all()


def test_equal_risk_shares():
    eligible = pd.DataFrame({'A': [True], 'B': [True]})
    shares = budgets(eligible, pd.DataFrame({'A': [0.1], 'B': [0.3]}))
    assert shares.iloc[0].tolist() == pytest.approx([0.75, 0.25])


def test_momentum_skips_the_last_month():
    index = pd.DataFrame({'A': np.arange(1.0, 300.0)})
    score = signals.momentum(index, 12, 1)
    assert score['A'].iloc[-1] == pytest.approx(index['A'].iloc[-22] / index['A'].iloc[-253] - 1)
    assert signals.ma_crossover(index, 1, 10)['A'].iloc[-1] > 0  # a rising price is above its average


def research_data() -> ResearchData:
    # 4 years of random prices: two equity indices, a bond fund and the pairs EURUSD, USDCHF, GBPUSD
    days = pd.bdate_range('2020-01-01', '2023-12-29')
    rng = np.random.default_rng(1)
    tickers = ['EQ1', 'EQ2', 'BD1', 'EURUSD=X', 'USDCHF=X', 'GBPUSD=X']
    prices = pd.DataFrame(
        np.exp(np.cumsum(rng.normal(0.0002, 0.01, (len(days), len(tickers))), axis=0)), index=days, columns=tickers
    )
    instruments = pd.DataFrame(
        {
            'group': ['Equity indices', 'Equity indices', 'Bonds', 'Currencies', 'Currencies', 'Currencies'],
            'currency': ['USD', 'EUR', 'USD', 'USD', 'CHF', 'USD'],
        },
        index=tickers,
    )
    rates = pd.DataFrame({'USD': 0.03, 'EUR': 0.02, 'CHF': 0.0, 'GBP': 0.04}, index=days)
    return ResearchData(prices=prices, instruments=instruments, cash_rates=rates)


def test_currencies_inverted_and_crossed_through_the_dollar():
    data = research_data()
    currencies = CurrencyIndices(data)
    assert currencies.currencies() == ['CHF', 'EUR', 'GBP', 'USD']
    pairs = currencies.pairs
    pd.testing.assert_series_equal(currencies.index('CHF', 'USD'), (1 / pairs['USDCHF=X']).rename('CHF/USD'))
    cross = currencies.index('GBP', 'CHF')  # GBP/USD x USD/CHF
    pd.testing.assert_series_equal(cross, (pairs['GBPUSD=X'] * pairs['USDCHF=X']).rename('GBP/CHF'))
    assert currencies.carry(['USD', 'GBP'], 'CHF').iloc[0].tolist() == pytest.approx([0.03, 0.04])


def test_every_default_strategy_runs(temp_db):
    data = research_data()
    sql.save_interest_rates('USD_GOV_10Y', pd.Series(0.04, index=data.prices.index[::5]))
    universes = build_universes(data)
    assert universes['Bonds'].carry['BD1'].dropna().iloc[-1] == pytest.approx(0.04 - 0.03)  # 10-year minus cash
    assert universes[CURRENCIES_UNIVERSE].excess.columns.tolist() == ['CHF', 'EUR', 'GBP']
    assert universes[HEDGING_PREFIX + 'CHF'].excess.columns.tolist() == ['USD', 'EUR', 'GBP']
    strategies = [s for s in default_strategies() if s.universe in universes]
    results = run_strategies(strategies, data)
    table = results.table()
    assert len(table) == len(strategies)
    # every time-series strategy of a universe with instruments has a result (cross sections need 3 instruments)
    with_instruments = table['universe'].isin(['Equity indices', 'Bonds', CURRENCIES_UNIVERSE, HEDGING_PREFIX + 'CHF'])
    time_series = table['strategy'].str.contains('TS,')
    assert table.loc[with_instruments & time_series, 'sharpe'].notna().all()
    # a long-only strategy is compared with its benchmark over the same days, a long/short one with cash
    trend = Strategy('Trend', 'Momentum 3M', 'Equity indices', lambda u: signals.momentum(u.index, 3))
    rows = run_strategies([trend, Strategy(**{**trend.__dict__, 'long_short': True})], data).table()
    assert not np.isnan(rows['benchmark_sharpe'].iloc[0]) and rows['benchmark_return'].iloc[1] == 0.0
