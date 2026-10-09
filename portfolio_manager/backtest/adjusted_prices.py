"""Adjusted prices of the portfolio and benchmark instruments, from the Yahoo Finance history stored in the database.

Online, the listing of each instrument is searched once and its history brought up to date (new days only); offline,
or if a download fails, the stored history is used.
"""

import logging
from typing import NamedTuple

import pandas as pd

from portfolio_manager.analytics.instruments import stored_adjusted_prices
from portfolio_manager.config.account_settings import Benchmark
from portfolio_manager.config.accounts import Account
from portfolio_manager.degiro.products import load_portfolio_products
from portfolio_manager.market_data.history import is_missing, refresh_history, search_listing
from portfolio_manager.market_data.yahoo import is_yahoo_reachable
from portfolio_manager.storage.queries import query_degiro_yahoo_map, save_benchmark_tickers, upsert_degiro_yahoo_map

logger = logging.getLogger(__name__)


class AdjPrices(NamedTuple):
    """Adjusted prices of the products of an account and where they come from."""

    prices: pd.DataFrame  # date x product id, account currency; products without adjusted prices are missing
    summary: str  # where the prices come from, for logs and the dashboard


def _listing(ticker: str | None, search: str | None, symbol: str | None, name: str | None) -> tuple[str | None, str]:
    # online: the stored listing brought up to date, or the listing searched (and stored) the first time;
    # (ticker, what was done), the ticker is None if no listing is found
    try:
        if ticker is None:
            ticker = search_listing(search=search, ticker=symbol, name=name)
            return ticker, 'found' if ticker is not None else 'not found'
        return ticker, refresh_history(ticker)
    except Exception as e:  # any download problem: the stored history is used
        logger.warning('Yahoo Finance download failed for %s: %s', ticker or search or symbol, e)
        return ticker, 'failed'


def get_portfolio_adj_prices(account: Account) -> AdjPrices:
    """Adjusted prices of the products of an account, in the account currency: products with the same ISIN (e.g. one
    ETF on two exchanges) share one Yahoo Finance listing; products without a listing are left out.
    """
    products_df = load_portfolio_products(account=account)
    online = is_yahoo_reachable()
    stored_map = query_degiro_yahoo_map(products_df['id'].to_list())
    groups = products_df.groupby(products_df['isin'].fillna(products_df['id'].astype(str)), sort=False)
    ticker_by_product: dict[int, str] = {}
    results = []
    for _, group in groups:
        product_ids = group['id'].astype(int).to_list()
        first = group.iloc[0]
        ticker = next((stored_map[p] for p in product_ids if p in stored_map), None)
        if online:
            ticker, result = _listing(
                ticker,
                search=None if is_missing(first['isin']) else first['isin'],
                symbol=None if is_missing(first['symbol']) else first['symbol'],
                name=None if is_missing(first['name']) else first['name'],
            )
            results.append(result)
            if result == 'found':
                upsert_degiro_yahoo_map(product_ids=product_ids, ticker=ticker)
        if ticker is not None:
            ticker_by_product.update({p: ticker for p in product_ids})

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
        if online:
            ticker, result = _listing(ticker, search=component.search, symbol=component.label, name=None)
            if result == 'found':
                found[component.label] = ticker
        if ticker is not None:
            tickers[component.label] = ticker
    if found:
        save_benchmark_tickers(account.name, found)
    base_df = stored_adjusted_prices(account, sorted(set(tickers.values())))
    labels = [c.label for c in benchmark.components]
    prices = pd.DataFrame({label: base_df[t] for label, t in tickers.items() if t in base_df.columns})
    return prices.reindex(columns=labels)
