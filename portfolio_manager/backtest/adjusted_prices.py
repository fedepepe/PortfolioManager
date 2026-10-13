"""Adjusted prices of the portfolio and benchmark instruments, from the Yahoo Finance history stored in the database.

Online, the listing of each instrument is searched once and its history brought up to date (new days only); offline,
or if a download fails, the stored history is used.
"""

import logging
from typing import NamedTuple

import pandas as pd

from portfolio_manager.analytics.instruments import (
    products_to_search,
    search_product_listing,
    stored_adjusted_prices,
)
from portfolio_manager.config.account_settings import Benchmark
from portfolio_manager.config.accounts import Account
from portfolio_manager.degiro.products import load_portfolio_products
from portfolio_manager.market_data.history import ListingSearches, refresh_history, search_listing, store_listing
from portfolio_manager.market_data.yahoo import is_yahoo_reachable
from portfolio_manager.storage.queries import (
    query_degiro_yahoo_map,
    query_listing_searches,
    save_benchmark_tickers,
    save_listing_search,
)

logger = logging.getLogger(__name__)


class AdjPrices(NamedTuple):
    """Adjusted prices of the products of an account and where they come from."""

    prices: pd.DataFrame  # date x product id, account currency; products without adjusted prices are missing
    summary: str  # where the prices come from, for logs and the dashboard


def _listing(ticker: str, store: bool = False, isin: str | None = None) -> tuple[str, str]:
    # online: the history of a listing brought up to date (stored with its information if new); (ticker, what was
    # done: new, updated, replaced, not found, failed): the stored history is used when a download fails
    try:
        if store:
            store_listing(ticker, isin=isin)
            return ticker, 'new'
        return ticker, refresh_history(ticker)
    except Exception as e:  # any download problem: the stored history is used
        logger.warning('Yahoo Finance download failed for %s: %s', ticker, e)
        return ticker, 'failed'


def get_portfolio_adj_prices(account: Account) -> AdjPrices:
    """Adjusted prices of the products of an account, in the account currency. Each product has its own Yahoo Finance
    listing (its line on its exchange first, see search_product_listing): searched when due (never searched, failed,
    not found a while ago), otherwise brought up to date; products without a listing are left out.
    """
    products_df = load_portfolio_products(account=account)
    online = is_yahoo_reachable()
    ticker_by_product = query_degiro_yahoo_map(products_df['id'].to_list())
    results = []
    if online:
        to_search = set(products_to_search(products_df)['id'])
        decided = query_listing_searches(list(to_search)).set_index('product_id')
        searches, stored = ListingSearches(), set()
        for product in products_df.itertuples():
            if product.id not in to_search:
                continue
            listing = search_product_listing(product, searches, decided)
            if listing.status != 'failed' or product.id not in ticker_by_product:
                save_listing_search([product.id], listing.ticker, listing.status, listing.rule)
            if listing.proposal is not None:
                logger.warning(
                    '%s: own line %s (%s) has another name: not used, to confirm with tasks.py '
                    'remap-portfolio-listings --apply from a terminal',
                    product.name,
                    listing.proposal,
                    listing.proposal_name,
                )
            results.append(listing.status)
            if listing.ticker is not None:
                ticker = _listing(listing.ticker, store=True, isin=product.isin)[0]
                ticker_by_product[product.id] = ticker
                stored.add(ticker)
        for ticker in sorted(set(ticker_by_product.values()) - stored):
            results.append(_listing(ticker)[1])

    base_df = stored_adjusted_prices(account, sorted(set(ticker_by_product.values())))
    prices = pd.DataFrame({p: base_df[t] for p, t in ticker_by_product.items() if t in base_df.columns})
    missing = [p for p in products_df['id'].astype(int) if p not in prices.columns]
    summary = _summary(online, results, base_df, missing)
    logger.info('%s adjusted prices: %s', account.name, summary)
    return AdjPrices(prices=prices, summary=summary)


def _summary(online: bool, results: list[str], base_df: pd.DataFrame, missing: list) -> str:
    # where the prices come from, e.g. "online: 14 updated, 1 found; 1 with unadjusted Degiro prices"
    counts = pd.Series(results, dtype=object).value_counts()
    parts = [', '.join(f'{n} {r}' for r, n in counts.items())] if online and results else []
    if not online and not base_df.empty:
        parts.append(f'{base_df.shape[1]} from the database (until {base_df.index[-1]:%Y-%m-%d})')
    if missing:
        parts.append(f'{len(set(missing))} with unadjusted Degiro prices')
    return f'{"online" if online else "offline"}: {"; ".join(parts)}'


def get_benchmark_adj_prices(account: Account, benchmark: Benchmark) -> pd.DataFrame:
    """Adjusted prices of the instruments of a benchmark, in the account currency: date x label, in the order of the
    instruments (an instrument without a listing is an empty column). The listing found for an instrument is kept.
    """
    online = is_yahoo_reachable()
    tickers, found = {}, {}
    for component in benchmark.components:
        ticker = component.ticker
        if online and ticker is None:
            try:
                ticker = search_listing(search=component.search, ticker=component.label)
            except Exception as e:  # any download problem: searched again next time
                logger.warning('Yahoo Finance search failed for %s: %s', component.search, e)
            if ticker is not None:
                found[component.label] = ticker
        elif online:
            _listing(ticker)
        if ticker is not None:
            tickers[component.label] = ticker
    if found:
        save_benchmark_tickers(account.name, found)
    base_df = stored_adjusted_prices(account, sorted(set(tickers.values())))
    labels = [c.label for c in benchmark.components]
    prices = pd.DataFrame({label: base_df[t] for label, t in tickers.items() if t in base_df.columns})
    return prices.reindex(columns=labels)
