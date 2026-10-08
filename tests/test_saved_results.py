import os

import pandas as pd
import pytest

import portfolio_manager.analytics.performance as performance
import portfolio_manager.backtest.workflows as workflows
import portfolio_manager.storage.tables as tables
from portfolio_manager.analytics.metrics import PerfDataTabs, compute_portfolio_metrics
from portfolio_manager.backtest.engine import backtest_portfolio
from portfolio_manager.storage.files import load_df_dict_from_excel


@pytest.fixture
def folders(tmp_path, monkeypatch):
    # data, results and Parquet folders in a temporary folder, instead of the project ones
    monkeypatch.setattr(workflows, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(performance, 'RESULTS_DIR', str(tmp_path / 'results'))
    monkeypatch.setattr(tables, 'DERIVED_DIR', str(tmp_path / 'data' / 'derived'))
    return tmp_path


@pytest.fixture
def backtest(prices):
    prices = prices.iloc[:300].rename(columns={'A0': 101, 'A1': 202, 'A2': 303, 'A3': 404})  # product ids
    data = backtest_portfolio(prices_df=prices, name='Test portfolio', target_exp=[0.25] * 4, freq_rebalancing='M')
    data.id_symbol_map = {101: 'AAA', 202: 'BBB', 303: 'CCC', 404: 'DDD'}
    return data


def test_backtest_saved_as_tables_and_visual_excel(folders, backtest):
    workflows.save_backtest_data(backtest)
    excel_files = sorted(os.listdir(folders / 'data'))
    assert excel_files == ['Test_portfolio_visual.xlsx', 'derived']  # no Excel copy with product ids
    loaded = workflows.load_backtest_data('Test portfolio')
    for field in ['nav', 'nav_eff', 'units', 'effective_weights', 'transaction_value', 'yield_total']:
        expected, value = getattr(backtest, field), getattr(loaded, field)
        if isinstance(expected, pd.Series):
            pd.testing.assert_series_equal(expected, value, check_freq=False)
        else:
            pd.testing.assert_frame_equal(expected, value, check_freq=False)
    assert loaded.id_symbol_map == backtest.id_symbol_map and loaded.freq == backtest.freq
    visual = load_df_dict_from_excel('Test portfolio_visual', folder_name=str(folders / 'data'))
    assert list(visual['units'].columns) == ['AAA', 'BBB', 'CCC', 'DDD', 'Cash']


def test_backtest_selected_fields_and_saved_at(folders, backtest):
    workflows.save_backtest_data(backtest)
    loaded = workflows.load_backtest_data('Test portfolio', fields=['nav_eff'])
    assert loaded.nav_eff is not None and loaded.units is None and loaded.prices is None
    assert workflows.backtest_saved_at('Test portfolio') > 0
    assert workflows.backtest_saved_at('Unknown') is None


def test_backtest_without_map_keeps_a_plain_excel_copy(folders, backtest):
    backtest.id_symbol_map = None
    workflows.save_backtest_data(backtest)
    assert 'Test_portfolio.xlsx' in os.listdir(folders / 'data')


def test_performance_results_load_as_from_excel(folders, backtest):
    results = compute_portfolio_metrics(hist_portfolio_data=backtest, print_results=False)
    performance.save_performance_data(results, file_name='Test portfolio')
    from_tables = performance.load_performance_data('Test portfolio')
    from_excel = load_df_dict_from_excel('Test portfolio', folder_name=str(folders / 'results'))
    # same tables, and series come back as one-column tables, as from the Excel file
    assert from_tables.keys() == from_excel.keys()
    for key in [PerfDataTabs.RISK_METRICS, PerfDataTabs.RETURNS_MONTHLY, PerfDataTabs.HIST_PERF_METRICS]:
        assert isinstance(from_tables[key], pd.DataFrame)
        pd.testing.assert_frame_equal(from_tables[key], from_excel[key], check_freq=False, check_names=False)
    assert from_tables[PerfDataTabs.RETURNS_MONTHLY]['return'].equals(results[PerfDataTabs.RETURNS_MONTHLY])


def test_loading_falls_back_to_excel_before_conversion(folders, backtest):
    results = compute_portfolio_metrics(hist_portfolio_data=backtest, print_results=False)
    performance.save_performance_data(results, file_name='Test portfolio')
    tables_folder = tables.derived_folder(performance.RESULTS, 'Test portfolio')
    os.remove(os.path.join(tables_folder, tables.DESCRIPTION_FILE))
    loaded = performance.load_performance_data('Test portfolio')
    assert PerfDataTabs.RISK_METRICS in loaded
