from datetime import date

import numpy as np
import pandas as pd
import pytest

import portfolio_manager.storage.queries as sql
from portfolio_manager.config.taa import TaaInstrument
from portfolio_manager.research import signals
from portfolio_manager.research.backtest import stop_at_ruin
from portfolio_manager.research.returns import ResearchData, load_research_data, stock_group
from portfolio_manager.research.strategies import (
    SECTOR_NEUTRAL_UNIVERSES,
    Strategy,
    build_universes,
    run_strategies,
    stock_history,
    stock_strategies,
    strategy_weights,
    within_sectors,
)

SP500 = stock_group('S&P 500')


def stock_data(n_stocks: int = 20) -> ResearchData:
    # 4 years of random prices of S&P 500 stocks in two sectors
    days = pd.bdate_range('2020-01-01', '2023-12-29')
    rng = np.random.default_rng(2)
    tickers = [f'S{n:02d}' for n in range(n_stocks)]
    prices = pd.DataFrame(
        np.exp(np.cumsum(rng.normal(0.0003, 0.015, (len(days), n_stocks)), axis=0)), index=days, columns=tickers
    )
    instruments = pd.DataFrame(
        {'group': SP500, 'currency': 'USD', 'sector': ['Tech' if n % 2 else 'Energy' for n in range(n_stocks)]},
        index=tickers,
    )
    return ResearchData(prices=prices, instruments=instruments, cash_rates=pd.DataFrame({'USD': 0.02}, index=days))


def test_members_join_the_research_data_with_their_sector(temp_db):
    days = pd.bdate_range('2024-01-01', periods=30)
    sql.save_taa_instruments([TaaInstrument('Equity indices', 'S&P 500', 'IDX')])
    sql.save_index_members(
        'S&P 500', pd.DataFrame({'ticker': ['AAA'], 'name': ['A'], 'sector': ['Tech']}), date(2024, 1, 5)
    )
    for ticker in ['IDX', 'AAA']:
        instrument_id = sql.upsert_yahoo_instrument(ticker, {'currency': 'USD'})
        sql.save_yahoo_prices(instrument_id, pd.DataFrame({'close': 10.0}, index=days))
    data = load_research_data()
    assert data.instruments.loc['AAA', ['group', 'sector']].tolist() == [SP500, 'Tech']
    assert data.instruments.loc['AAA', 'currency'] == 'USD' and pd.isna(data.instruments.loc['IDX', 'sector'])
    assert list(load_research_data(include_stocks=False).prices.columns) == ['IDX']


def test_low_volatility_and_52_week_high():
    days = pd.bdate_range('2020-01-01', periods=300)
    excess = pd.DataFrame({'CALM': np.tile([0.001, -0.001], 150), 'WILD': np.tile([0.02, -0.02], 150)}, index=days)
    score = signals.low_volatility(excess).iloc[-1]
    assert score['CALM'] > score['WILD']
    index = pd.DataFrame({'A': np.r_[np.linspace(1, 2, 200), np.linspace(2, 1.5, 100)]}, index=days)
    assert signals.high_proximity(index)['A'].iloc[-1] == pytest.approx(1.5 / 2 - 1)


def test_sector_neutral_ranks_each_stock_within_its_sector():
    score = pd.DataFrame({'T1': [3.0], 'T2': [1.0], 'E1': [-1.0], 'E2': [-2.0]})
    sectors = pd.Series({'T1': 'Tech', 'T2': 'Tech', 'E1': 'Energy', 'E2': 'Energy'})
    assert within_sectors(score, sectors).iloc[0].tolist() == [1.0, 0.5, 1.0, 0.5]


def test_stock_strategies_hold_the_top_fifth():
    universes = build_universes(stock_data())
    universe = universes[SP500]
    momentum = Strategy('Relative momentum', 'M', SP500, lambda x: signals.momentum(x.index, 12, 1), 'xs', fraction=0.2)
    held = (strategy_weights(momentum, universe).iloc[-1] > 0).sum()
    assert held == 4  # a fifth of 20 stocks
    neutral = strategy_weights(Strategy(**{**momentum.__dict__, 'sector_neutral': True}), universe).iloc[-1]
    # ranked within each sector: the top fifth of the ranks, as many stocks from each sector
    chosen = neutral[neutral > 0].index
    assert {universe.sectors[t] for t in chosen} == {'Tech', 'Energy'}


def test_every_stock_strategy_runs(temp_db):
    strategies = [s for s in stock_strategies() if s.universe == SP500]
    assert SP500 in SECTOR_NEUTRAL_UNIVERSES and any(s.sector_neutral for s in strategies)
    table = run_strategies(strategies, stock_data()).table()
    assert len(table) == len(strategies) == 24
    short_history = table['strategy'].str.contains('Long-term reversal')  # needs 3 years before the first signal
    assert table.loc[~short_history, 'sharpe'].notna().all()
    assert table['strategy'].str.contains('sector-neutral').sum() == 12


def test_stocks_studied_once_half_of_todays_members_have_prices():
    days = pd.bdate_range('2020-01-01', periods=6)
    excess = pd.DataFrame({'A': 0.01, 'B': 0.01, 'C': 0.01, 'D': 0.01}, index=days)
    excess.iloc[:2, 1] = np.nan  # B starts on the third day
    excess.iloc[:4, 2:] = np.nan  # C and D on the fifth
    assert stock_history(excess).index[0] == days[2]  # A and B: half of the members
    assert stock_history(excess.iloc[:2]).empty  # A alone


def test_long_short_strategy_stopped_when_its_value_falls_to_zero():
    returns = stop_at_ruin(np.array([0.1, -0.6, -1.2, 0.5, 0.2]))
    assert returns.tolist() == [0.1, -0.6, -1.0, 0.0, 0.0]  # the third day loses more than what is left
    assert stop_at_ruin(np.array([0.1, -0.5])).tolist() == [0.1, -0.5]
