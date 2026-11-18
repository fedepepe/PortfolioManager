import json

import pandas as pd
import pytest

import portfolio_manager.dashboard.saa_data as data
from portfolio_manager.config.accounts import Account, Brokers
from portfolio_manager.config.allocations import Allocation, AllocationInstrument, Bucket

CHF = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')


def balanced() -> Allocation:
    return Allocation(
        name='Balanced',
        class_targets={'Equity': 0.6, 'Fixed Income': 0.4},
        splits={'Equity': 'region'},
        buckets=[Bucket('Equity', 'Switzerland', 0.25), Bucket('Equity', 'Global', 0.75)],
        instruments=[
            AllocationInstrument('CHSPI.SW', 'CH0237935652', 'Equity', 'Switzerland', 1.0),
            AllocationInstrument('VWRL.SW', 'IE00B3RBWM25', 'Equity', 'Global', 1.0),
            AllocationInstrument('CHCORP.SW', None, 'Fixed Income', None, 1.0),
        ],
    )


def test_bucket_keys():
    assert data.bucket_key('Equity', 'Switzerland') == 'Equity|Switzerland'
    assert data.parse_bucket_key('Fixed Income|') == ('Fixed Income', None)
    assert data.parse_bucket_key(data.bucket_key('Equity', 'Euro Area')) == ('Equity', 'Euro Area')


def test_class_weights_from_percentages():
    allocation = data.set_class_targets(balanced(), {'Equity': data.fraction(70), 'Fixed Income': 0.3, 'Crypto': 0.0})
    assert allocation.class_targets == pytest.approx({'Equity': 0.7, 'Fixed Income': 0.3})  # no Crypto at 0%
    assert data.fraction(None) == 0.0 and data.fraction('') == 0.0


def test_a_new_split_starts_without_parts_or_instruments():
    allocation = data.set_split(balanced(), 'Equity', 'region')  # unchanged: everything kept
    assert len(allocation.buckets) == 2 and len(allocation.instruments) == 3
    allocation = data.set_split(allocation, 'Equity', 'sub_class')
    assert allocation.splits['Equity'] == 'sub_class' and allocation.buckets == []
    assert [i.ticker for i in allocation.instruments] == ['CHCORP.SW']  # only the bonds are left
    allocation = data.set_split(allocation, 'Equity', None)
    assert 'Equity' not in allocation.splits


def test_parts_at_zero_dropped():
    allocation = data.set_bucket_weights(balanced(), 'Equity', {'Switzerland': 0.4, 'Global': 0.6, 'US': 0.0})
    assert allocation.buckets == [Bucket('Equity', 'Switzerland', 0.4), Bucket('Equity', 'Global', 0.6)]


def test_a_changed_choice_starts_from_equal_weights():
    allocation = balanced()
    allocation.instruments[1] = AllocationInstrument('VWRL.SW', 'IE00B3RBWM25', 'Equity', 'Global', 0.7)
    same = data.set_bucket_selection(allocation, 'Equity', 'Global', [('VWRL.SW', 'IE00B3RBWM25')])
    assert same.bucket_instruments('Equity', 'Global')[0].weight == 0.7  # the same choice keeps its weights
    changed = data.set_bucket_selection(allocation, 'Equity', 'Global', [('VWRL.SW', 'X'), ('SWDA.SW', 'Y')])
    assert [(i.ticker, i.weight) for i in changed.bucket_instruments('Equity', 'Global')] == [
        ('VWRL.SW', 0.5),
        ('SWDA.SW', 0.5),
    ]
    changed = data.set_instrument_weights(changed, {'VWRL.SW': 0.8, 'SWDA.SW': 0.2})
    assert changed.target_weights()['SWDA.SW'] == pytest.approx(0.6 * 0.75 * 0.2)


def test_summary_with_manual_and_optimized_weights():
    allocation = balanced()
    assert data.summary_rows(allocation) == [
        (0, 'Equity', '60%'),
        (1, 'Switzerland', '15%'),
        (2, 'CHSPI.SW', '15%'),
        (1, 'Global', '45%'),
        (2, 'VWRL.SW', '45%'),
        (0, 'Fixed Income', '40%'),
        (1, 'CHCORP.SW', '40%'),
    ]
    allocation.weight_mode = 'optimize'
    rows = data.summary_rows(allocation)
    assert rows[0] == (0, 'Equity', '55%–65%') and rows[2] == (2, 'CHSPI.SW', 'optimized')


def test_candidates_with_the_chosen_ones_first(monkeypatch):
    calls = []

    def fake_candidates(account, asset_class, split, value, account_currency_only, include_leveraged, search, limit):
        calls.append((value, account_currency_only, search))
        if search == 'VWRL.SW':  # looked up by its ticker, outside the filters
            return pd.DataFrame([{'ticker': 'VWRL.SW', 'isin': 'IE00B3RBWM25', 'name': 'Vanguard', 'MAX_DD': 0.3}])
        rows = [('SWDA.SW', 'iShares', 0.2), ('CSPX.SW', 'S&P 500', float('nan'))]
        return pd.DataFrame([{'ticker': t, 'isin': None, 'name': n, 'MAX_DD': d} for t, n, d in rows])

    monkeypatch.setattr(data, 'bucket_candidates', fake_candidates)
    rows = data.candidate_rows(CHF, balanced(), 'Equity', 'Global')
    assert [r['ticker'] for r in rows] == ['VWRL.SW', 'SWDA.SW', 'CSPX.SW']
    assert rows[2]['MAX_DD'] is None  # missing values as None (plain data for the grid)
    assert calls[0] == ('Global', True, None) and calls[1] == ('Global', False, 'VWRL.SW')
    json.dumps(rows)


def test_completed_steps():
    allocation = balanced()
    assert data.completed_steps(allocation) == {'classes': True, 'splits': True, 'instruments': True}
    allocation.instruments.pop()  # the bonds have no instrument
    assert data.completed_steps(allocation) == {'classes': True, 'splits': True, 'instruments': False}
    allocation.buckets.pop()  # the equity sub-classes add up to 25%: the next step is not complete either
    assert data.completed_steps(allocation) == {'classes': True, 'splits': False, 'instruments': False}
    allocation.class_targets['Equity'] = 0.7
    assert not any(data.completed_steps(allocation).values())


def test_summary_with_the_decimals_of_the_weights():
    allocation = balanced()
    allocation.class_targets = {'Equity': 0.61, 'Fixed Income': 0.39}
    rows = data.summary_rows(allocation)
    assert rows[0] == (0, 'Equity', '61%') and rows[1] == (1, 'Switzerland', '15.25%')  # 61% x 25%


def test_total_colors():
    assert data.weights_total_class(1.0) == 'text-success'
    assert data.weights_total_class(1.05) == 'text-danger' and data.weights_total_class(0.9) == 'text-warning'


def test_new_allocation_from_the_balanced_preset():
    allocation = data.new_allocation()
    assert allocation.name == '' and allocation.class_targets == data.PRESETS['Balanced']
    assert allocation.settings.min_pf_exposure == 1.0 and allocation.settings.max_vol is None


def test_comparison_saved_with_the_backtest_and_read_back(tmp_path, monkeypatch):
    import numpy as np

    import portfolio_manager.storage.tables as tables

    monkeypatch.setattr(tables, 'DERIVED_DIR', str(tmp_path))
    days = pd.bdate_range('2023-01-02', periods=300)
    nav = pd.Series(np.linspace(1000, 1200, len(days)), index=days)
    computed = []

    def fake_comparison(account, index):
        computed.append(len(index))
        return pd.DataFrame({'Benchmark': np.linspace(100, 110, len(index))}, index=index)  # starts at 100

    monkeypatch.setattr(data, 'comparison_navs', fake_comparison)
    navs, metrics = data._comparison(CHF, 'Portfolio CHF SAA Test', 1.0, nav)
    assert (
        computed == [300]
        and list(navs.columns) == ['Allocation', 'Benchmark']
        and navs.iloc[0].tolist() == [100.0, 100.0]
    )
    again_navs, again_metrics = data._comparison(CHF, 'Portfolio CHF SAA Test', 1.0, nav)  # read back, not computed
    assert computed == [300]
    pd.testing.assert_frame_equal(again_navs, navs, check_freq=False)
    for label in metrics:
        pd.testing.assert_series_equal(again_metrics[label], metrics[label], check_dtype=False)
    data._comparison(CHF, 'Portfolio CHF SAA Test', 2.0, nav)  # a new run of the backtest: computed again
    assert computed == [300, 300]


def test_one_lock_per_key():
    from portfolio_manager.utils.locks import key_lock

    assert key_lock(('a', 1)) is key_lock(('a', 1)) and key_lock(('a', 1)) is not key_lock(('a', 2))


def test_start_explained_by_the_instruments_with_the_shortest_history(monkeypatch):
    days = pd.bdate_range('2021-05-20', periods=100)
    weights = pd.DataFrame(0.25, index=days, columns=['CHSPI.SW', 'V3GX.SW', 'IGLC.SW', 'Cash'])
    navs = pd.DataFrame({'Allocation': 100.0}, index=days)
    backtest = data.SavedBacktest(navs=navs, weights=weights, metrics={}, saved_at=None)
    firsts = {'CHSPI.SW': '2016-10-10', 'V3GX.SW': '2021-05-20', 'IGLC.SW': '2020-11-10'}
    asked = []

    def fake_firsts(tickers):
        asked.append(tickers)
        return pd.Series({t: pd.Timestamp(firsts[t]) for t in tickers})

    monkeypatch.setattr(data, 'query_first_close_dates', fake_firsts)
    assert data.start_text(backtest) == 'It starts on 2021-05-20 because V3GX.SW has prices only from 2021-05-20.'
    assert asked[-1] == ['CHSPI.SW', 'V3GX.SW', 'IGLC.SW']  # not the cash
    firsts['IGLC.SW'] = '2021-05-20'  # two instruments start on the same day
    later = data.SavedBacktest(
        navs=navs.set_axis(days + pd.Timedelta(days=92)), weights=weights, metrics={}, saved_at=None
    )
    assert data.start_text(later) == (
        'It starts on 2021-08-20 because IGLC.SW and V3GX.SW have prices only from 2021-05-20, '
        'and the optimizer needs 3 months of history to compute the initial weights.'
    )


def test_deleting_an_allocation_removes_only_its_saved_backtest(tmp_path, monkeypatch):
    import os

    import portfolio_manager.storage.tables as tables

    monkeypatch.setattr(tables, 'DERIVED_DIR', str(tmp_path / 'derived'))
    monkeypatch.setattr(data, 'DATA_DIR', str(tmp_path / 'data'))
    os.makedirs(tmp_path / 'data')
    paths = {name: data.saved_backtest_paths(CHF, name) for name in ['Growth', 'Growth 2']}
    for folder_tables, folder_comparison, excel in paths.values():
        for folder in (folder_tables, folder_comparison):
            os.makedirs(folder)
            open(os.path.join(folder, 'tables.json'), 'w').close()
        open(excel, 'w').close()
    portfolio = os.path.join(tmp_path / 'derived', 'backtests', 'Portfolio_CHF')  # the account's own backtest
    os.makedirs(portfolio)
    assert data.delete_saved_backtest(CHF, 'Growth') == paths['Growth']
    assert not any(os.path.exists(p) for p in paths['Growth'])
    assert all(os.path.exists(p) for p in paths['Growth 2']) and os.path.isdir(portfolio)  # the others are kept
    assert data.delete_saved_backtest(CHF, 'Growth') == [] and data.delete_saved_backtest(CHF, ' ') == []
