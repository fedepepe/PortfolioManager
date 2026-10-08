"""Tables (data frames and series) stored as Parquet files, one file per table, in one folder per result.

A description file next to the tables records what Parquet cannot store as is, so that the tables are loaded back
exactly as they were saved: series, non-text column names (e.g. product ids), indexes that are neither dates nor text
(e.g. years with a 'Total' row), columns mixing value types, and attributes of the result.
"""

import datetime
import json
import os

import pandas as pd

from portfolio_manager.config.settings import DERIVED_DIR
from portfolio_manager.storage.files import to_file_name

DESCRIPTION_FILE = 'tables.json'
_INDEX_COLUMN = '__index__'
_TIMESTAMP_KEY = '__timestamp__'


def _labels_are_text(labels: pd.Index) -> bool:
    return all(isinstance(label, str) for label in labels)


def _to_json_value(value):
    # JSON keeps int, float, str, bool and None apart (NaN is written as NaN); dates are tagged
    if isinstance(value, (pd.Timestamp, datetime.datetime, datetime.date)):
        return {_TIMESTAMP_KEY: pd.Timestamp(value).isoformat()}
    if hasattr(value, 'item'):  # numpy scalars
        value = value.item()
    return value


def _from_json_value(value):
    if isinstance(value, dict) and _TIMESTAMP_KEY in value:
        return pd.Timestamp(value[_TIMESTAMP_KEY])
    return value


def _encode(table: pd.DataFrame | pd.Series) -> tuple[pd.DataFrame, dict]:
    """Frame that Parquet can store, and the description needed to restore the original."""
    desc = {}
    if isinstance(table, pd.Series):
        desc['series_name'] = _to_json_value(table.name)
        table = table.to_frame(name='value')
    frame = table.copy()
    desc['columns_name'] = _to_json_value(frame.columns.name)
    if not _labels_are_text(frame.columns):
        desc['columns'] = [_to_json_value(c) for c in frame.columns]
    frame.columns = [str(c) for c in frame.columns]
    # columns whose values mix types (e.g. text and numbers) are stored as JSON text
    mixed = [c for c in frame.columns if frame[c].dtype == object and frame[c].map(type).nunique() > 1]
    for column in mixed:
        frame[column] = frame[column].map(lambda v: json.dumps(_to_json_value(v)))
    if mixed:
        desc['json_columns'] = mixed
    desc['index_name'] = _to_json_value(frame.index.name)
    if isinstance(frame.index, pd.DatetimeIndex) or _labels_are_text(frame.index):
        frame.index.name = _INDEX_COLUMN
    else:
        desc['index'] = [_to_json_value(i) for i in frame.index]
        frame = frame.reset_index(drop=True)
    return frame, desc


def _decode(frame: pd.DataFrame, desc: dict) -> pd.DataFrame | pd.Series:
    for column in desc.get('json_columns', []):
        frame[column] = frame[column].map(lambda v: _from_json_value(json.loads(v)))
    if 'index' in desc:
        values = [_from_json_value(i) for i in desc['index']]
        frame.index = pd.Index(values, dtype=object if len({type(i) for i in values}) > 1 else None)
    frame.index.name = desc.get('index_name')
    if 'columns' in desc:
        frame.columns = pd.Index([_from_json_value(c) for c in desc['columns']])
    frame.columns.name = desc.get('columns_name')
    if 'series_name' in desc:
        series = frame.iloc[:, 0]
        series.name = desc['series_name']
        return series
    return frame


def derived_folder(kind: str, name: str) -> str:
    """Folder of the tables of a derived result, e.g. ('backtests', 'Portfolio CHF')."""
    return os.path.join(DERIVED_DIR, kind, to_file_name(name))


def save_tables(folder: str, tables: dict[str, pd.DataFrame | pd.Series | None], attributes: dict | None = None):
    """Save the tables (None values are skipped) and the attributes (JSON values) of a result in its folder.

    The description file is written last, so that a result is never read half-written; tables of a previous save
    that are not in this one are removed.
    """
    os.makedirs(folder, exist_ok=True)
    descriptions = {}
    for name, table in tables.items():
        if table is None:
            continue
        frame, descriptions[name] = _encode(table)
        frame.to_parquet(os.path.join(folder, f'{name}.parquet'))
    for file in os.listdir(folder):
        if file.endswith('.parquet') and file[: -len('.parquet')] not in descriptions:
            os.remove(os.path.join(folder, file))
    with open(os.path.join(folder, DESCRIPTION_FILE), 'w', encoding='utf-8') as f:
        json.dump({'tables': descriptions, 'attributes': attributes or {}}, f, indent=1)


def has_tables(folder: str) -> bool:
    """True if the folder holds saved tables."""
    return os.path.isfile(os.path.join(folder, DESCRIPTION_FILE))


def tables_version(folder: str) -> float:
    """Modification time of the saved tables (0 if there are none): it changes at every save."""
    path = os.path.join(folder, DESCRIPTION_FILE)
    return os.path.getmtime(path) if os.path.isfile(path) else 0.0


def load_attributes(folder: str) -> dict:
    """Attributes saved with the tables."""
    with open(os.path.join(folder, DESCRIPTION_FILE), encoding='utf-8') as f:
        return json.load(f)['attributes']


def load_tables(folder: str, names: list[str] | None = None) -> dict[str, pd.DataFrame | pd.Series]:
    """Saved tables (all, or the given ones that exist), exactly as they were saved."""
    with open(os.path.join(folder, DESCRIPTION_FILE), encoding='utf-8') as f:
        descriptions = json.load(f)['tables']
    names = list(descriptions) if names is None else [n for n in names if n in descriptions]
    return {n: _decode(pd.read_parquet(os.path.join(folder, f'{n}.parquet')), descriptions[n]) for n in names}
