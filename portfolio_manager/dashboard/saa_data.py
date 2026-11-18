"""Data of the Strategic Asset Allocation page: changes to the allocation being edited (kept in the browser as plain
data), the candidates of its buckets, its summary and its saved backtest.
"""

import json
import logging
import math
import os
import shutil
import threading
from dataclasses import dataclass, replace
from functools import lru_cache

import pandas as pd

from portfolio_manager.analytics.metrics import PerfDataTabs, compute_portfolio_metrics
from portfolio_manager.analytics.saa import CANDIDATES_SHOWN, bucket_candidates
from portfolio_manager.backtest.saa import comparison_navs, saa_backtest_name
from portfolio_manager.backtest.workflows import BACKTESTS, account_benchmark, load_backtest_data
from portfolio_manager.config.account_settings import Benchmark, BenchmarkComponent
from portfolio_manager.config.accounts import Account
from portfolio_manager.config.allocations import (
    PRESETS,
    TOLERANCE,
    Allocation,
    AllocationInstrument,
    Bucket,
    percent_text,
)
from portfolio_manager.config.settings import DATA_DIR
from portfolio_manager.storage.files import to_file_name
from portfolio_manager.storage.queries import query_first_close_dates
from portfolio_manager.storage.tables import derived_folder, has_tables, load_attributes, load_tables, save_tables
from portfolio_manager.utils.locks import key_lock

logger = logging.getLogger(__name__)

# how each class may be split (split -> label); the other classes are not split
SPLIT_OPTIONS = {
    'Equity': {'region': 'Region', 'sub_class': 'Segment'},
    'Fixed Income': {'sub_class': 'Type', 'region': 'Region'},
    'Commodities': {'sub_class': 'Group'},
    'Crypto': {'sub_class': 'Group'},
}
ALLOCATION_LABEL = 'Allocation'
MIN_METRICS_DAYS = 60  # shorter series get no metrics
COMPARISONS = 'saa_comparisons'  # kind of derived result: the comparison of a saved backtest
CASH_LABEL = 'Cash'  # column of the cash in the weights of a backtest
# a backtest starting this much after the first prices of its last instrument waited for the optimizer's history
OPTIMIZER_HISTORY_GAP_DAYS = 20


def new_allocation() -> Allocation:
    """The allocation a new one starts from: the balanced class weights, nothing split or chosen."""
    return Allocation(name='', class_targets=dict(PRESETS['Balanced']))


# BUCKET KEYS: a bucket as one text (the ids of the page's controls)
def bucket_key(asset_class: str, value: str | None) -> str:
    """Key of a bucket, e.g. 'Equity|Switzerland' ('Fixed Income|' for a class that is not split)."""
    return f'{asset_class}|{value or ""}'


def parse_bucket_key(key: str) -> tuple[str, str | None]:
    """The class and the value of a bucket key (None for a class that is not split)."""
    asset_class, value = key.split('|', 1)
    return asset_class, value or None


# CHANGES TO THE ALLOCATION (percentages from the controls; empty controls count as 0)
def fraction(percent) -> float:
    """A percentage typed in a control as a fraction (empty: 0)."""
    return 0.0 if percent in (None, '') else float(percent) / 100.0


def set_class_targets(allocation: Allocation, targets: dict[str, float]) -> Allocation:
    """The class weights (fractions); classes at 0% are dropped."""
    allocation.class_targets = {c: w for c, w in targets.items() if w > 0}
    return allocation


def set_split(allocation: Allocation, asset_class: str, split: str | None) -> Allocation:
    """Split a class (None: not split); a new split starts without weights or instruments for the class."""
    if allocation.splits.get(asset_class) == split:
        return allocation
    if split:
        allocation.splits[asset_class] = split
    else:
        allocation.splits.pop(asset_class, None)
    allocation.buckets = [b for b in allocation.buckets if b.asset_class != asset_class]
    allocation.instruments = [i for i in allocation.instruments if i.asset_class != asset_class]
    return allocation


def set_bucket_weights(allocation: Allocation, asset_class: str, weights: dict[str, float]) -> Allocation:
    """The weights (fractions of the class) of the parts of a split class; parts at 0% are dropped."""
    others = [b for b in allocation.buckets if b.asset_class != asset_class]
    allocation.buckets = others + [Bucket(asset_class, v, w) for v, w in weights.items() if w > 0]
    return allocation


def set_bucket_selection(
    allocation: Allocation, asset_class: str, bucket: str | None, chosen: list[tuple[str, str | None]]
) -> Allocation:
    """The instruments (ticker, ISIN) chosen for a bucket, in order; a changed choice starts from equal weights."""
    current = allocation.bucket_instruments(asset_class, bucket)
    if [i.ticker for i in current] == [t for t, _ in chosen]:
        return allocation
    weight = 1.0 / len(chosen) if chosen else 0.0
    others = [i for i in allocation.instruments if (i.asset_class, i.bucket) != (asset_class, bucket)]
    allocation.instruments = others + [AllocationInstrument(t, isin, asset_class, bucket, weight) for t, isin in chosen]
    return allocation


def set_instrument_weights(allocation: Allocation, weights: dict[str, float]) -> Allocation:
    """The weights (fractions of their bucket) of the chosen instruments."""
    allocation.instruments = [
        replace(i, weight=weights[i.ticker]) if i.ticker in weights else i for i in allocation.instruments
    ]
    return allocation


# CANDIDATES OF A BUCKET
def _candidates(
    account: Account,
    asset_class: str,
    split: str | None,
    value: str | None,
    currency_only: bool,
    leveraged: bool,
    search: str | None,
) -> pd.DataFrame:
    # cached by analytics.saa (once a day)
    return bucket_candidates(
        account,
        asset_class,
        split=split,
        value=value,
        account_currency_only=currency_only,
        include_leveraged=leveraged,
        search=search,
        limit=CANDIDATES_SHOWN,
    )


def _records(df: pd.DataFrame) -> list[dict]:
    """Rows as plain data (missing values: None)."""
    return [
        {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}
        for row in df.to_dict('records')
    ]


def candidate_rows(
    account: Account,
    allocation: Allocation,
    asset_class: str,
    bucket: str | None,
    currency_only: bool = True,
    leveraged: bool = False,
    search: str | None = None,
) -> list[dict]:
    """The candidates of a bucket, the chosen instruments first (also when the filters or the search leave them out),
    then the most traded others.
    """
    split = allocation.splits.get(asset_class)
    search = (search or '').strip() or None
    found = _candidates(account, asset_class, split, bucket, currency_only, leveraged, search)
    chosen_rows = []
    for instrument in allocation.bucket_instruments(asset_class, bucket):
        row = found[found['ticker'] == instrument.ticker]
        if row.empty:  # left out by the filters: looked up by its ticker
            row = _candidates(account, asset_class, split, bucket, False, True, instrument.ticker)
            row = row[row['ticker'] == instrument.ticker]
        if row.empty:
            row = pd.DataFrame([{'ticker': instrument.ticker, 'isin': instrument.isin, 'name': ''}])
        chosen_rows.append(row)
    others = found[~found['ticker'].isin([i.ticker for i in allocation.bucket_instruments(asset_class, bucket)])]
    return _records(pd.concat([*chosen_rows, others], ignore_index=True)) if chosen_rows else _records(others)


# STEPS
def weights_total_class(total: float) -> str:
    """Text color of a total of weights: green at 100%, red above, yellow below."""
    if abs(total - 1) <= TOLERANCE:
        return 'text-success'
    return 'text-danger' if total > 1 else 'text-warning'


def completed_steps(allocation: Allocation) -> dict[str, bool]:
    """Whether each of the first three steps is complete: the class weights add up to 100%; the sub-classes of
    every divided class too; every part has instruments (with manual weights adding up to 100%). Each step needs the
    ones before it.
    """
    targets = allocation.class_targets.values()
    classes = all(w >= 0 for w in targets) and abs(sum(targets) - 1) <= TOLERANCE
    sub_classes = classes and all(
        abs(sum(b.weight for b in allocation.buckets if b.asset_class == c) - 1) <= TOLERANCE
        for c in allocation.active_classes()
        if c in allocation.splits
    )
    instruments = sub_classes
    for asset_class in allocation.active_classes():
        for bucket in allocation.class_buckets(asset_class):
            chosen = allocation.bucket_instruments(asset_class, bucket.value)
            manual_total = sum(i.weight for i in chosen)
            if not chosen or (allocation.weight_mode == 'manual' and abs(manual_total - 1) > TOLERANCE):
                instruments = False
    return {'classes': classes, 'splits': sub_classes, 'instruments': instruments}


def has_saved_backtest(account: Account, allocation_name: str) -> bool:
    """Whether the allocation has a saved backtest."""
    return bool(allocation_name) and has_tables(derived_folder(BACKTESTS, saa_backtest_name(account, allocation_name)))


def saved_backtest_paths(account: Account, allocation_name: str) -> list[str]:
    """Where the saved backtest of an allocation is: its tables, its comparison (COMPARISONS) and its Excel copy."""
    name = saa_backtest_name(account, allocation_name)
    excel = os.path.join(DATA_DIR, f'{to_file_name(name)}.xlsx')
    return [derived_folder(BACKTESTS, name), derived_folder(COMPARISONS, name), excel]


def delete_saved_backtest(account: Account, allocation_name: str) -> list[str]:
    """Delete the saved backtest of an allocation (when the allocation is deleted); returns what was removed."""
    if not allocation_name.strip():
        return []
    removed = []
    for path in saved_backtest_paths(account, allocation_name):
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed.append(path)
        elif os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    if removed:
        logger.info('Saved backtest of %s removed: %s', allocation_name, ', '.join(removed))
    return removed


# BENCHMARK
def can_be_benchmark(allocation: Allocation) -> bool:
    """Whether the allocation can become the benchmark of the account: manual weights, all of them set (the first three
    steps complete).
    """
    return allocation.weight_mode == 'manual' and all(completed_steps(allocation).values())


def benchmark_from_allocation(allocation: Allocation) -> Benchmark:
    """The allocation as a benchmark: its instruments with their weights in the portfolio, rebalanced like it."""
    components = [
        BenchmarkComponent(label=ticker, search=ticker, weight=weight, ticker=ticker)
        for ticker, weight in allocation.target_weights().items()
        if weight > 0
    ]
    return Benchmark(components=components, rebalancing_freq=allocation.rebalancing_freq)


def benchmark_key(account: Account) -> str:
    """The benchmark of an account as text: a comparison saved for another benchmark is computed again."""
    try:
        benchmark = account_benchmark(account)
    except ValueError:  # no benchmark
        return ''
    return json.dumps([benchmark.rebalancing_freq, [[c.search, c.weight] for c in benchmark.components]])


# SUMMARY
def summary_rows(allocation: Allocation) -> list[tuple[int, str, str]]:
    """The allocation as indented rows (level, label, weight): classes, their parts and their instruments, with the
    weights in the portfolio (manual weights), or the class ranges (optimized weights).
    """
    rows = []
    manual = allocation.weight_mode == 'manual'
    weights = allocation.target_weights() if manual else {}
    bounds = allocation.class_bounds()
    for asset_class in allocation.active_classes():
        target = allocation.class_targets[asset_class]
        if manual or asset_class not in bounds:
            rows.append((0, asset_class, percent_text(target)))
        else:
            low, high = bounds[asset_class]
            rows.append((0, asset_class, f'{percent_text(low)}–{percent_text(high)}'))
        for bucket in allocation.class_buckets(asset_class):
            level = 1
            if bucket.value is not None:
                rows.append((1, bucket.value, percent_text(target * bucket.weight) if manual else ''))
                level = 2
            for instrument in allocation.bucket_instruments(asset_class, bucket.value):
                weight = percent_text(weights[instrument.ticker]) if manual else 'optimized'
                rows.append((level, instrument.ticker, weight))
    return rows


# SAVED BACKTEST
@dataclass
class SavedBacktest:
    """The saved backtest of an allocation with the series it is compared with (each starting at 100), the effective
    weights by date, the risk metrics of each series (over its own dates) and when it was computed.
    """

    navs: pd.DataFrame
    weights: pd.DataFrame
    metrics: dict
    saved_at: float | None


def saved_backtest(account: Account, allocation_name: str) -> SavedBacktest | None:
    """The saved backtest of an allocation (None if it has none)."""
    if not has_saved_backtest(account, allocation_name):
        return None
    name = saa_backtest_name(account, allocation_name)
    saved_at = load_attributes(derived_folder(BACKTESTS, name)).get('saved_at')
    benchmark = benchmark_key(account)
    with key_lock(('saved backtest', account, name, saved_at, benchmark)):  # computed once when two threads ask
        return _saved_backtest(account, name, saved_at, benchmark)


@lru_cache(maxsize=8)
def _saved_backtest(account: Account, name: str, saved_at: float | None, benchmark: str) -> SavedBacktest:
    data = load_backtest_data(name, fields=['cum_time_weighted_return', 'effective_weights'])
    growth = data.cum_time_weighted_return.dropna().add(1.0)  # rebased to 100 in the comparison
    navs, metrics = _comparison(account, name, saved_at, benchmark, growth)
    return SavedBacktest(navs=navs, weights=data.effective_weights, metrics=metrics, saved_at=saved_at)


def _comparison(
    account: Account, name: str, saved_at: float | None, benchmark: str, nav: pd.Series
) -> tuple[pd.DataFrame, dict]:
    """The allocation, the benchmark and the portfolio over the dates of a backtest (each starting at 100) and their
    risk metrics: saved with the backtest (COMPARISONS), computed and saved the first time, and again after a change
    of the benchmark (benchmark_key).
    """
    folder = derived_folder(COMPARISONS, name)
    saved = load_attributes(folder) if has_tables(folder) else {}
    if saved.get('backtest_saved_at') == saved_at and saved.get('benchmark') == benchmark:
        tables = load_tables(folder)
        metrics_df = tables['metrics']
        return tables['navs'], {label: metrics_df[label].rename(label) for label in metrics_df.columns}
    navs = pd.concat(
        [(100.0 * nav / nav.iloc[0]).rename(ALLOCATION_LABEL), comparison_navs(account, nav.index)], axis=1
    )
    metrics = {}
    for label in navs.columns:
        series = navs[label].dropna()
        if len(series) >= MIN_METRICS_DAYS:
            perf = compute_portfolio_metrics(nav=series, compute_hist_metrics=False, print_results=False)
            metrics[label] = perf[PerfDataTabs.RISK_METRICS].rename(label)
    attributes = {'backtest_saved_at': saved_at, 'benchmark': benchmark}
    save_tables(folder, {'navs': navs, 'metrics': pd.DataFrame(metrics)}, attributes=attributes)
    return navs, metrics


def start_limit(backtest: SavedBacktest) -> tuple[list[str], pd.Timestamp] | None:
    """The instruments of a backtest whose prices start last, which set its start, and their first day (None if no
    instrument has prices).
    """
    firsts = query_first_close_dates([c for c in backtest.weights.columns if c != CASH_LABEL])
    if firsts.empty:
        return None
    last = firsts.max()
    return sorted(firsts[firsts == last].index), last


def start_text(backtest: SavedBacktest) -> str:
    """Why the backtest starts when it does: the instruments with the shortest history (and, with optimized weights,
    the history the optimizer needs before its first weights).
    """
    limit = start_limit(backtest)
    if limit is None:
        return ''
    tickers, first_day = limit
    start = backtest.navs.index[0]
    names = ' and '.join(tickers)
    text = f'It starts on {start:%Y-%m-%d} because {names} {"has" if len(tickers) == 1 else "have"} prices only from '
    text += f'{first_day:%Y-%m-%d}'
    if start - first_day > pd.Timedelta(days=OPTIMIZER_HISTORY_GAP_DAYS):
        text += ', and the optimizer needs 3 months of history to compute the initial weights'
    return text + '.'


# WARM-UP
def warm_up(account: Account, allocation: Allocation, filters: tuple[str, ...] = ('currency',)):
    """Compute in the background (cached) what steps 3 and 4 of an allocation show: the candidates of its buckets with
    the default filters, and its saved backtest; the page then shows them at once.
    """

    def run():
        try:
            for asset_class in allocation.active_classes():
                for bucket in allocation.class_buckets(asset_class):
                    candidate_rows(
                        account, allocation, asset_class, bucket.value, 'currency' in filters, 'leveraged' in filters
                    )
            if allocation.name:
                saved_backtest(account, allocation.name)
        except Exception:
            logger.exception('Warm-up of the allocation %s failed', allocation.name)

    threading.Thread(target=run, name='saa-warm-up', daemon=True).start()
