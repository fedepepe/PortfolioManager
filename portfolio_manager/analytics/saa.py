"""Strategic asset allocation: the instruments that can be chosen for each part (bucket) of an allocation.

The candidates of a bucket are the Yahoo listings of the classified funds of its asset class (and of its region or
sub-class when the class is split) with recent prices, by decreasing average traded value in the account currency;
leveraged and inverse products are left out unless asked for.

The listings of a bucket with their traded values, and the metrics of each listing, are computed once a day: kept in
memory and saved for the day (CACHE_KIND), so that a search, a change of the bucket or a restart of the dashboard only
computes what is new.
"""

import hashlib
import math
import os
import shutil
import threading
from datetime import date
from functools import lru_cache

import pandas as pd

from portfolio_manager.analytics.instruments import (
    average_traded_values,
    compute_product_performance,
    stored_adjusted_prices,
    traded_values_in_account_currency,
)
from portfolio_manager.analytics.metrics import Metrics
from portfolio_manager.config.accounts import Account
from portfolio_manager.storage.queries import query_etf_classes, query_isin_listings, query_yahoo_instruments
from portfolio_manager.storage.tables import derived_folder, has_tables, load_tables, save_tables
from portfolio_manager.utils.locks import key_lock

CANDIDATES_SHOWN = 20  # candidates of a bucket shown (the others through the search)
# the home region of the funds of an account, offered first
HOME_REGIONS = {'CHF': 'Switzerland', 'EUR': 'Euro Area'}
# metrics shown for each candidate
CANDIDATE_METRICS = [
    Metrics.LAST_YEAR_RETURN,
    Metrics.ANN_3Y_RETURN,
    Metrics.VOLATILITY,
    Metrics.SHARPE_RATIO,
    Metrics.MAX_DD,
]
CANDIDATE_COLUMNS = ['ticker', 'isin', 'name', 'currency', 'traded_value', 'hedged', 'hedge_currency']
CACHE_KIND = 'saa_candidates'  # kind of derived result: the listings and metrics computed today (older days removed)


def _day_cache(day: date, name: str) -> str:
    """Folder of a result cached for a day."""
    return derived_folder(CACHE_KIND, f'{day.isoformat()}_{name}')


def _save_day_cache(day: date, name: str, tables: dict[str, pd.DataFrame]):
    """Save a result for the day, removing the results of the days before."""
    folder = _day_cache(day, name)
    parent = os.path.dirname(folder)
    if os.path.isdir(parent):
        for old in os.listdir(parent):
            if not old.startswith(day.isoformat()):
                shutil.rmtree(os.path.join(parent, old), ignore_errors=True)
    save_tables(folder, tables)


def class_funds(asset_class: str, include_leveraged: bool = False) -> pd.DataFrame:
    """The classified funds of an asset class (etf_classes rows by ISIN), without the leveraged and inverse ones."""
    classes = query_etf_classes()
    funds = classes[classes['asset_class'] == asset_class]
    return funds if include_leveraged else funds[~funds['leveraged'].astype(bool)]


def split_values(account: Account, asset_class: str, split: str) -> pd.Series:
    """The values a class can be split into (its regions or sub-classes) with their number of funds, the home region of
    the account first, then by decreasing number of funds.
    """
    counts = class_funds(asset_class)[split].dropna().value_counts()
    home = HOME_REGIONS.get(account.currency)
    order = sorted(counts.index, key=lambda v: (v != home if split == 'region' else True, -counts[v], v))
    return counts.reindex(order)


@lru_cache(maxsize=64)
def _bucket_listings(
    account: Account,
    asset_class: str,
    split: str | None,
    value: str | None,
    account_currency_only: bool,
    include_leveraged: bool,
    day: date,  # computed again every day
) -> pd.DataFrame:
    """The listings of a bucket with recent prices, the most traded first (see bucket_candidates), without metrics;
    computed once a day (saved for the day).
    """
    key = repr((account.name, account.currency, asset_class, split, value, account_currency_only, include_leveraged))
    name = 'listings_' + hashlib.sha1(key.encode()).hexdigest()[:16]
    if has_tables(_day_cache(day, name)):
        return load_tables(_day_cache(day, name))['listings']
    table = _compute_bucket_listings(account, asset_class, split, value, account_currency_only, include_leveraged)
    _save_day_cache(day, name, {'listings': table})
    return table


def _compute_bucket_listings(
    account: Account,
    asset_class: str,
    split: str | None,
    value: str | None,
    account_currency_only: bool,
    include_leveraged: bool,
) -> pd.DataFrame:
    funds = class_funds(asset_class, include_leveraged)
    if split is not None:
        funds = funds[funds[split] == value]
    listings = query_isin_listings(funds.index.to_list())
    if listings.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    averages = average_traded_values(listings['ticker'].to_list())  # listings with recent prices only
    if account_currency_only:
        averages = averages[averages['currency'] == account.currency]
    traded = traded_values_in_account_currency(account, averages)
    table = listings.set_index('ticker').reindex(traded.index)
    info = query_yahoo_instruments(table.index.to_list()).reindex(table.index)
    table['name'] = info['name_long'].fillna(info['name_short']).fillna(table['isin'].map(funds['name']))
    table['currency'] = averages['currency']
    table['traded_value'] = traded
    table['hedged'] = table['isin'].map(funds['hedged']).fillna(False).astype(bool)
    table['hedge_currency'] = table['isin'].map(funds['hedge_currency'])
    table = table.sort_values('traded_value', ascending=False)
    return table.rename_axis('ticker').reset_index()[CANDIDATE_COLUMNS]


_metrics_cache: dict[tuple[Account, date, str], dict[str, float]] = {}
_metrics_lock = threading.Lock()  # one computation at a time (a page and the warm-up of an allocation)
_metrics_loaded: set[tuple[Account, date]] = set()  # accounts whose metrics of the day were read from the day cache


def _cached_metrics(account: Account, tickers: list[str]) -> pd.DataFrame:
    """candidate_metrics of listings, computed once a day per listing (listings without history: missing values)."""
    with _metrics_lock:
        return _cached_metrics_unlocked(account, tickers)


def _cached_metrics_unlocked(account: Account, tickers: list[str]) -> pd.DataFrame:
    today = date.today()
    for cached in [k for k in _metrics_cache if k[1] != today]:  # the days before
        del _metrics_cache[cached]
    columns = [m.name for m in CANDIDATE_METRICS]
    name = f'metrics_{account.name}'
    if (account, today) not in _metrics_loaded:  # the metrics computed today before a restart
        _metrics_loaded.add((account, today))
        if has_tables(_day_cache(today, name)):
            for ticker, row in load_tables(_day_cache(today, name))['metrics'].iterrows():
                _metrics_cache[(account, today, ticker)] = row.to_dict()
    missing = [t for t in tickers if (account, today, t) not in _metrics_cache]
    if missing:
        computed = candidate_metrics(account, missing)
        for ticker in missing:
            row = computed.loc[ticker] if ticker in computed.index else pd.Series(math.nan, index=columns)
            _metrics_cache[(account, today, ticker)] = row.to_dict()
        known = {k[2]: v for k, v in _metrics_cache.items() if k[0] == account and k[1] == today}
        _save_day_cache(today, name, {'metrics': pd.DataFrame.from_dict(known, orient='index', columns=columns)})
    rows = {t: _metrics_cache[(account, today, t)] for t in tickers}
    return pd.DataFrame.from_dict(rows, orient='index', columns=columns).astype(float)


def clear_caches():
    """Forget the cached listings and metrics in memory (the day cache on disk stays)."""
    _bucket_listings.cache_clear()
    _metrics_cache.clear()
    _metrics_loaded.clear()


def clear_day_cache():
    """Forget the listings and metrics computed today, in memory and on disk (after new prices were stored): they are
    computed again at their next use.
    """
    with _metrics_lock:
        clear_caches()
        shutil.rmtree(os.path.dirname(_day_cache(date.today(), 'any')), ignore_errors=True)


def bucket_candidates(
    account: Account,
    asset_class: str,
    split: str | None = None,
    value: str | None = None,
    account_currency_only: bool = True,
    include_leveraged: bool = False,
    search: str | None = None,
    limit: int = CANDIDATES_SHOWN,
) -> pd.DataFrame:
    """The listings that can be chosen for a bucket (an asset class, or the part of a split class whose region or
    sub-class is value), with recent prices: in the account currency only if asked, matching the search text (ticker,
    ISIN or name) if given; the most traded first (average daily traded value of the last 3 months, in the account
    currency), at most limit, with their performance metrics in the account currency.
    """
    args = (account, asset_class, split, value, account_currency_only, include_leveraged, date.today())
    with key_lock(('bucket listings', *args)):  # computed once when asked for by two threads at the same time
        table = _bucket_listings(*args).copy()
    if search and search.strip():
        text = search.strip().lower()
        matches = table['ticker'].str.lower().str.contains(text, regex=False)
        matches |= table['isin'].fillna('').str.lower().str.contains(text, regex=False)
        matches |= table['name'].fillna('').str.lower().str.contains(text, regex=False)
        table = table[matches]
    table = table.head(limit).reset_index(drop=True)
    return table.join(_cached_metrics(account, table['ticker'].to_list()), on='ticker')


def candidate_metrics(account: Account, tickers: list[str]) -> pd.DataFrame:
    """Performance metrics of listings in the account currency (by ticker; listings without history are left out)."""
    columns = [m.name for m in CANDIDATE_METRICS]
    prices = stored_adjusted_prices(account, tickers)
    if prices.empty:
        return pd.DataFrame(columns=columns)
    perf = compute_product_performance(prices).set_index('Ticker')
    return perf.reindex(columns=columns).astype(float)
