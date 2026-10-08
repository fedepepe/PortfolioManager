import numpy as np
import pandas as pd

from portfolio_manager.storage.tables import (
    has_tables,
    load_attributes,
    load_tables,
    save_tables,
    tables_version,
)


def assert_same(a, b):
    if isinstance(a, pd.Series):
        pd.testing.assert_series_equal(a, b, check_freq=False)
    else:
        pd.testing.assert_frame_equal(a, b, check_freq=False)


DAYS = pd.bdate_range('2021-01-01', periods=5)
TABLES = {
    'by_product_id': pd.DataFrame(np.arange(10.0).reshape(5, 2), index=DAYS, columns=[19748466, 4815045]),
    'nav': pd.Series([1.0, 2.0, np.nan, 4.0, 5.0], index=DAYS, name='NAV'),
    'yearly': pd.Series(
        [0.1, -0.05, 0.04], index=pd.Index(['Total', 2021, 2020], dtype=object, name='Year'), name='return'
    ),
    'text_index': pd.DataFrame({'value': [1.5, 2.5]}, index=['Sharpe ratio', 'Volatility']),
    'int_index': pd.DataFrame({'x': [1, 2]}, index=pd.Index([7, 9], name='id')),
    'mixed_values': pd.DataFrame({'AAA.L': ['USD', 1.0, None], 'BBB': ['EUR', 2, 'x']}, index=['ccy', 'n', 'other']),
    'empty': pd.DataFrame(columns=['a', 'b'], dtype=float),
    'unnamed_series': pd.Series([1, 2, 3]),
}


def test_round_trip_keeps_types(tmp_path):
    save_tables(str(tmp_path), TABLES)
    loaded = load_tables(str(tmp_path))
    assert loaded.keys() == TABLES.keys()
    for name, table in TABLES.items():
        assert_same(table, loaded[name])
    assert list(loaded['by_product_id'].columns) == [19748466, 4815045]
    assert loaded['mixed_values'].loc['n', 'AAA.L'] == 1.0 and loaded['mixed_values'].loc['ccy', 'BBB'] == 'EUR'


def test_load_some_tables_and_attributes(tmp_path):
    save_tables(str(tmp_path), TABLES, attributes={'freq': 'B', 'saved_at': 123.5, 'map': [[1, 'X']]})
    assert list(load_tables(str(tmp_path), names=['nav', 'missing'])) == ['nav']
    assert load_attributes(str(tmp_path)) == {'freq': 'B', 'saved_at': 123.5, 'map': [[1, 'X']]}


def test_new_save_replaces_old_tables(tmp_path):
    save_tables(str(tmp_path), TABLES)
    save_tables(str(tmp_path), {'nav': TABLES['nav'], 'skipped': None})
    assert list(load_tables(str(tmp_path))) == ['nav']
    assert sorted(p.name for p in tmp_path.iterdir()) == ['nav.parquet', 'tables.json']


def test_version_and_presence(tmp_path):
    folder = str(tmp_path / 'result')
    assert not has_tables(folder) and tables_version(folder) == 0.0
    save_tables(folder, {'nav': TABLES['nav']})
    assert has_tables(folder) and tables_version(folder) > 0.0


def test_dates_in_an_index_or_column_with_other_values(tmp_path):
    year_ends = pd.Index([pd.Timestamp('2020-12-31'), pd.Timestamp('2021-12-31'), 'Total'], dtype=object)
    tables = {
        'yields': pd.DataFrame({'A': [0.01, 0.02, 0.03]}, index=year_ends),
        'mixed': pd.DataFrame({'when': [pd.Timestamp('2021-01-01'), 'text']}),
    }
    save_tables(str(tmp_path), tables)
    loaded = load_tables(str(tmp_path))
    for name, table in tables.items():
        assert_same(table, loaded[name])
