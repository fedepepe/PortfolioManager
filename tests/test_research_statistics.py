import numpy as np
import pandas as pd
import pytest
from test_research_strategies import research_data

from portfolio_manager.research.statistics import (
    alpha_regression,
    deflated_sharpe,
    expected_max_sharpe,
    fama_macbeth,
    mean_test,
    one_sided,
    signal_and_forward_returns,
    strategy_tests,
    time_series_regressions,
    uncovered_interest_parity,
)
from portfolio_manager.research.strategies import CURRENCIES_UNIVERSE, default_strategies, run_strategies

MONTHS = pd.date_range('2000-01-31', periods=240, freq='M')


def test_forward_returns_over_the_next_period():
    days = pd.bdate_range('2024-01-01', '2024-04-30')
    index = pd.DataFrame({'A': np.linspace(100.0, 120.0, len(days))}, index=days)
    signal, forward = signal_and_forward_returns(index, index)
    assert signal.index[0] == pd.Timestamp('2024-01-31')
    assert forward.iloc[0, 0] == pytest.approx(index.loc['2024-02-29', 'A'] / index.loc['2024-01-31', 'A'] - 1)
    assert np.isnan(forward.iloc[-1, 0])  # no next period


def test_time_series_regression_finds_a_predictive_signal():
    rng = np.random.default_rng(0)
    signal = pd.DataFrame(rng.normal(size=(240, 3)), index=MONTHS, columns=['A', 'B', 'C'])
    forward = 0.3 * signal + pd.DataFrame(rng.normal(size=(240, 3)), index=MONTHS, columns=signal.columns)
    forward['C'] = rng.normal(size=240)  # C: no relation
    per_instrument, pooled = time_series_regressions(signal, forward)
    assert per_instrument.loc['A', 't_stat'] > 3 and abs(per_instrument.loc['C', 't_stat']) < 2.5
    assert pooled.t_stat > 3 and pooled.n_obs == 720


def test_fama_macbeth_on_the_ranking_of_the_instruments():
    rng = np.random.default_rng(1)
    columns = list('ABCDEFGH')
    signal = pd.DataFrame(rng.normal(size=(240, 8)), index=MONTHS, columns=columns)
    noise = pd.DataFrame(rng.normal(scale=0.05, size=(240, 8)), index=MONTHS, columns=columns)
    good = fama_macbeth(signal, 0.02 * signal + noise)
    assert good.t_stat > 3 and good.n_obs == 240
    assert abs(fama_macbeth(signal, noise).t_stat) < 2.5


def test_alpha_of_a_strategy_beyond_its_exposure():
    rng = np.random.default_rng(2)
    benchmark = pd.Series(rng.normal(0.005, 0.04, 240), index=MONTHS)
    returns = 0.004 + 0.5 * benchmark + pd.Series(rng.normal(0, 0.005, 240), index=MONTHS)
    fit = alpha_regression(returns, benchmark)
    assert fit.beta == pytest.approx(0.5, abs=0.05) and fit.alpha == pytest.approx(0.004, abs=0.001)
    assert fit.t_stat > 5 and fit.skill.mean() == pytest.approx(fit.alpha, abs=1e-12)


def test_tests_of_the_mean_and_one_sided_p_values():
    assert mean_test(pd.Series([0.01] * 10)).n_obs == 0  # too short
    assert one_sided(0.04, 2.1) == pytest.approx(0.02) and one_sided(0.04, -2.1) == pytest.approx(0.98)


def test_every_strategy_tested_and_adjusted_for_the_number_tried(temp_db):
    data = research_data()
    strategies = [s for s in default_strategies() if s.universe in ('Equity indices', CURRENCIES_UNIVERSE)]
    results = run_strategies(strategies, data)
    tests = strategy_tests(results)
    assert len(tests) == len(strategies)
    tested = tests['skill_p'].notna()
    assert tested.sum() > len(tests) // 2
    assert (tests.loc[tested, 'skill_p_holm'] >= tests.loc[tested, 'skill_p'] - 1e-12).all()
    long_only = tests[[not s.long_short for s in strategies]]
    assert long_only['beta'].notna().any() and tests[[s.long_short for s in strategies]]['beta'].isna().all()
    parity = uncovered_interest_parity(results)
    assert list(parity.index) == ['CHF', 'EUR', 'GBP'] and parity['months'].min() > 40


def test_deflated_sharpe_ratio_harder_with_more_trials():
    assert expected_max_sharpe(0.01, 200) > expected_max_sharpe(0.01, 10) > 0
    rng = np.random.default_rng(3)
    returns = pd.Series(rng.normal(0.01, 0.03, 240))  # monthly Sharpe ratio about 0.33
    assert deflated_sharpe(returns, 0.0) > 0.99
    assert deflated_sharpe(returns, expected_max_sharpe(0.04, 200)) < deflated_sharpe(returns, 0.0)
