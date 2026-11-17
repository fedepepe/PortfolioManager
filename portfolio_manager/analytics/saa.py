"""Strategic asset allocation: the instruments that can be chosen for each part (bucket) of an allocation.

The candidates of a bucket are the Yahoo listings of the classified funds of its asset class (and of its region or
sub-class when the class is split) with recent prices, by decreasing average traded value in the account currency;
leveraged and inverse products are left out unless asked for.
"""

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
    funds = class_funds(asset_class, include_leveraged)
    if split is not None:
        funds = funds[funds[split] == value]
    listings = query_isin_listings(funds.index.to_list())
    if listings.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS + [m.name for m in CANDIDATE_METRICS])
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
    if search and search.strip():
        text = search.strip().lower()
        matches = table.index.str.lower().str.contains(text, regex=False)
        matches |= table['isin'].fillna('').str.lower().str.contains(text, regex=False)
        matches |= table['name'].fillna('').str.lower().str.contains(text, regex=False)
        table = table[matches]
    table = table.sort_values('traded_value', ascending=False).head(limit)
    table = table.rename_axis('ticker').reset_index()[CANDIDATE_COLUMNS]
    return table.join(candidate_metrics(account, table['ticker'].to_list()), on='ticker')


def candidate_metrics(account: Account, tickers: list[str]) -> pd.DataFrame:
    """Performance metrics of listings in the account currency (by ticker; listings without history are left out)."""
    columns = [m.name for m in CANDIDATE_METRICS]
    prices = stored_adjusted_prices(account, tickers)
    if prices.empty:
        return pd.DataFrame(columns=columns)
    perf = compute_product_performance(prices).set_index('Ticker')
    return perf.reindex(columns=columns).astype(float)
