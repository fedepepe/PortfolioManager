import numpy as np
import pandas as pd
import pytest
from test_research_strategies import research_data

import portfolio_manager.research.report as report
from portfolio_manager.research.evaluation import combination, skill_returns, skill_sharpe, walk_forward
from portfolio_manager.research.strategies import Strategy
from portfolio_manager.storage.tables import derived_folder, load_attributes, load_tables

DAYS = pd.bdate_range('2010-01-01', '2015-12-31')


def variant(name: str) -> Strategy:
    return Strategy('Trend', name, 'Equity indices', signal=lambda u: u.index)


def test_walk_forward_follows_the_best_variant_so_far():
    rng = np.random.default_rng(0)
    good = pd.Series(rng.normal(0.001, 0.01, len(DAYS)), index=DAYS)  # skilled from the start
    bad = pd.Series(rng.normal(-0.001, 0.01, len(DAYS)), index=DAYS)
    returns, choices = walk_forward({variant('Good'): good, variant('Bad'): bad}, benchmark=None)
    # chosen from 2013 on: 3 years of history needed
    assert list(choices.index) == [2013, 2014, 2015] and set(choices) == {'Good'}
    pd.testing.assert_series_equal(returns, good[good.index.year >= 2013], check_names=False)


def test_combination_holds_every_variant_in_equal_parts():
    a = pd.Series([0.01, 0.02], index=DAYS[:2])
    b = pd.Series([0.03], index=DAYS[1:2])  # starts a day later
    assert combination({variant('A'): a, variant('B'): b}).tolist() == pytest.approx([0.01, 0.025])


def test_skill_is_what_remains_beyond_the_exposure_to_the_benchmark():
    rng = np.random.default_rng(1)
    benchmark = pd.Series(rng.normal(0.0004, 0.01, len(DAYS)), index=DAYS)
    half = 0.5 * benchmark  # half the benchmark: no skill at all
    assert skill_returns(half, benchmark).abs().max() == pytest.approx(0.0, abs=1e-12)
    assert np.isnan(skill_sharpe(half, benchmark))
    extra = pd.Series(rng.normal(0.0004, 0.001, len(DAYS)), index=DAYS)  # an extra return of about 10% a year
    assert skill_sharpe(half + extra, benchmark) > 3


def test_report_written_and_saved(temp_db, tmp_path):
    built = report.build_report(research_data())
    assert len(built.strategies) > 100 and {'skill_p_holm', 'skill_sharpe_second_half'} <= set(built.strategies)
    assert {'wf_alpha', 'combo_skill_t', 'wf_choices'} <= set(built.families)
    path = str(tmp_path / 'taa_research.xlsx')
    report.write_report(built, path)
    sheets = pd.read_excel(path, sheet_name=None)
    assert list(sheets) == ['Families', 'Strategies', 'Interest parity', 'Notes']
    assert len(sheets['Strategies']) == len(built.strategies)
    report.save_report(built)
    folder = derived_folder(report.DERIVED_KIND, report.DERIVED_NAME)
    assert len(load_tables(folder)['families']) == len(built.families)
    assert load_attributes(folder)['created_at'] == built.created_at.isoformat(timespec='seconds')
