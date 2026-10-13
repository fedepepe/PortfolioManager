"""Yahoo Finance daily history stored in the database: listing search, incremental downloads and adjusted prices.

Each listing is searched once (by ISIN, then by ticker) and its history stored; later downloads only fetch the days
since the last stored one, plus a few stored days to check them: if Yahoo changed them (a split, a correction), the
whole history is downloaded again. The adjusted close is computed from the close and the distributions (dividends,
capital gains), as Yahoo computes it; the prices are already adjusted for splits.
"""

import logging
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import NamedTuple

import numpy as np
import pandas as pd
import yfinance as yf

from portfolio_manager.market_data.yahoo import Exchanges, get_ticker_info, search_ticker
from portfolio_manager.storage.queries import (
    YAHOO_PRICE_COLUMNS,
    query_yahoo_instruments,
    query_yahoo_last_date,
    query_yahoo_prices,
    save_yahoo_prices,
    upsert_yahoo_instrument,
)

logger = logging.getLogger(__name__)

HISTORY_PERIOD = '10y'  # history downloaded for a new listing
OVERLAP_DAYS = 10  # stored days downloaded again (checked, and completed when Yahoo publishes a day late)
# Yahoo columns of the history and of the information, and their names in the database
HISTORY_COLUMNS = {
    'Open': 'open',
    'High': 'high',
    'Low': 'low',
    'Close': 'close',
    'Volume': 'volume',
    'Dividends': 'dividend',
    'Stock Splits': 'split',
    'Capital Gains': 'capital_gain',
}
INFO_FIELDS = {
    'shortName': 'name_short',
    'longName': 'name_long',
    'currency': 'currency',
    'quoteType': 'quote_type',
    'exchange': 'exchange',
    'market': 'market',
    'region': 'region',
}
MIN_NAME_SIMILARITY = 0.6  # listings found by ticker must have a more similar name than this
ISIN_PATTERN = re.compile(r'[A-Z]{2}[A-Z0-9]{9}[0-9]')
# currencies quoted by Yahoo in hundredths of the currency: pence, South African cents, Israeli agorot
MINOR_UNITS = {'GBp': 'GBP', 'GBX': 'GBP', 'ZAc': 'ZAR', 'ILA': 'ILS'}


def is_missing(value) -> bool:
    """True for None, NaN and blank strings."""
    if isinstance(value, str):
        return not value.strip()
    return value is None or pd.isna(value)


def major_currency(currency: str) -> tuple[str, float]:
    """Currency of the prices quoted in a Yahoo currency, and the factor converting them (0.01 for pence)."""
    if currency in MINOR_UNITS:
        return MINOR_UNITS[currency], 0.01
    return currency.upper(), 1.0


def download_history(ticker: str, start: date | None = None) -> pd.DataFrame:
    """Daily history of a listing from a day (10 years without a start): YAHOO_PRICE_COLUMNS by date (dates of the
    exchange), corporate actions only on the days they happen; empty if Yahoo has none.
    """
    period = {'start': start} if start is not None else {'period': HISTORY_PERIOD}
    df = yf.Ticker(ticker).history(interval='1d', auto_adjust=False, actions=True, **period)
    if df.empty:
        return pd.DataFrame(columns=YAHOO_PRICE_COLUMNS, index=pd.DatetimeIndex([]))
    df = df.rename(columns=HISTORY_COLUMNS).reindex(columns=YAHOO_PRICE_COLUMNS)
    index = pd.DatetimeIndex(df.index)
    df.index = (index.tz_localize(None) if index.tz is not None else index).normalize()
    df = df[~df.index.duplicated(keep='last')].sort_index()
    for column in ['dividend', 'split', 'capital_gain']:
        df[column] = df[column].where(df[column] != 0)
    return df


def stored_history(ticker: str, start: date | None = None) -> pd.DataFrame:
    """Stored daily history of a listing (YAHOO_PRICE_COLUMNS by date)."""
    return query_yahoo_prices([ticker], start=start).set_index('date').drop(columns='ticker')


def changed_days(stored: pd.DataFrame, new: pd.DataFrame) -> pd.DatetimeIndex:
    """Stored days whose close or corporate actions differ in a new download; the last stored day is not checked (it
    may have been downloaded during the day).
    """
    days = stored.index[:-1].intersection(new.index)
    columns = ['close', 'dividend', 'split', 'capital_gain']
    old_values, new_values = stored.loc[days, columns].fillna(0.0), new.loc[days, columns].fillna(0.0)
    same = np.isclose(old_values.to_numpy(float), new_values.to_numpy(float), rtol=1e-9, atol=1e-12).all(axis=1)
    return days[~same]


def refresh_history(ticker: str, full: bool = False) -> str:
    """Bring the stored history of a listing up to date: the new days (and the last stored ones, to check them); the
    whole history if none is stored, if full, or if Yahoo changed stored days. Returns what was done: 'new',
    'updated', 'replaced' or 'not found' (the stored history is kept when Yahoo returns nothing).
    """
    instrument_id = upsert_yahoo_instrument(ticker)
    last = query_yahoo_last_date(instrument_id)
    if last is not None and not full:
        start = last - timedelta(days=OVERLAP_DAYS)
        new = download_history(ticker, start=start)
        changed = changed_days(stored_history(ticker, start=start), new)
        if changed.empty:
            save_yahoo_prices(instrument_id, new)
            return 'updated'
        logger.info('%s: Yahoo Finance changed %d stored days: whole history downloaded again', ticker, len(changed))
    new = download_history(ticker)
    if new.empty:
        return 'not found'
    save_yahoo_prices(instrument_id, new, replace=True)
    return 'new' if last is None else 'replaced'


def refresh_info(ticker: str, isin: str | None = None):
    """Download and save the information of a listing (name, currency, exchange, ...)."""
    info = get_ticker_info(ticker) or {}
    fields = {INFO_FIELDS[k]: v for k, v in info.items() if k in INFO_FIELDS}
    if isin is None:
        isin = yf.Ticker(ticker).isin
    fields['isin'] = None if is_missing(isin) or isin == '-' else isin
    upsert_yahoo_instrument(ticker, fields)


def on_preferred_exchange(ticker: str) -> bool:
    """Whether a listing is on one of the preferred exchanges (its ticker ends with their suffix)."""
    return any(ticker.endswith(f'.{x.code}') for x in Exchanges)


def name_similarity(name: str, other: str) -> float:
    """Similarity of two instrument names, from 0 to 1 (case ignored)."""
    return SequenceMatcher(None, name.lower(), other.lower()).ratio()


class Listing(NamedTuple):
    """Result of the search of a listing: ticker (None if none), status (found, not found, failed), the rule that chose
    it (own line, own line (confirmed), preferred exchange, currency, name, symbol search, other exchange) and, when
    the own line has prices but another name, that own line and its name, proposed to the user for confirmation.
    """

    ticker: str | None
    status: str
    rule: str | None = None
    proposal: str | None = None
    proposal_name: str | None = None


class ListingSearches:
    """Searches and price probes of one run, kept so that products of the same fund repeat neither."""

    def __init__(self):
        self.quotes: dict[str, list[dict] | None] = {}
        self.last_prices: dict[str, pd.Timestamp | None] = {}
        self.currencies: dict[str, str | None] = {}

    def search(self, text: str) -> list[dict] | None:
        """Listings found by a search text (an ISIN, a ticker, ...); None if Yahoo did not answer."""
        if text not in self.quotes:
            results = search_ticker(text)
            self.quotes[text] = results.quotes if results is not None else None
        return self.quotes[text]

    def last_price(self, ticker: str) -> pd.Timestamp | None:
        """Month of the last price of a listing in the last 10 years (monthly prices: one light request); None if it
        has none.
        """
        if ticker not in self.last_prices:
            listing = yf.Ticker(ticker)
            closes = listing.history(period=HISTORY_PERIOD, interval='1mo', auto_adjust=False)['Close']
            self.last_prices[ticker] = closes.last_valid_index() if not closes.dropna().empty else None
            self.currencies[ticker] = (listing.history_metadata or {}).get('currency')
        return self.last_prices[ticker]

    def currency(self, ticker: str) -> str | None:
        """Currency of the prices of a listing (pence counted as pounds, see major_currency), from its probe."""
        self.last_price(ticker)
        currency = self.currencies.get(ticker)
        return major_currency(currency)[0] if currency else None

    def name(self, ticker: str) -> str:
        """Yahoo name of a listing, from the search of its ticker (from its information if the search misses it)."""
        quotes = self.search(ticker) or []
        label = next((q.get('longname') or q.get('shortname') for q in quotes if q.get('symbol') == ticker), None)
        if not label:
            info = get_ticker_info(ticker) or {}
            label = info.get('longName') or info.get('shortName')
        return label or ''


def exchange_rank(ticker: str) -> int:
    """Position of the exchange of a listing in the preferred order (the other exchanges last)."""
    return next((n for n, x in enumerate(Exchanges) if ticker.endswith(f'.{x.code}')), len(Exchanges))


def find_listing(
    search: str | None = None,
    ticker: str | None = None,
    name: str | None = None,
    exchange: str | None = None,
    currency: str | None = None,
    accepted: str | None = None,
    rejected: str | None = None,
    searches: ListingSearches | None = None,
) -> Listing:
    """Find the Yahoo Finance listing of an instrument (nothing is stored). The candidates are the listings found by the
    search text (usually the ISIN) that have prices; when none is on a preferred exchange, also those of the ticker
    with a name similar to the given one.

    The choice: the ticker on its own exchange (the Yahoo suffix of the exchange of the instrument, e.g. EUNL.DE for
    EUNL on Xetra) if the search finds it; probed directly otherwise, and used if its name is similar or the user
    accepted it (an own line with prices and another name is proposed for confirmation, unless rejected). Otherwise the
    listings on the preferred exchanges if any, among them those in the currency of the instrument if any, then the
    closest name, then the listings of the ticker itself, then the preferred exchange order.
    """
    search, ticker, name = (None if is_missing(v) else v for v in (search, ticker, name))
    searches = searches or ListingSearches()
    names, by_symbol, failed = {}, set(), False
    proposal = proposal_name = None

    def add(quotes: list[dict] | None, guard: bool) -> list[str]:
        # the listings with prices among the quotes (with a similar name if guarded); remembers their names
        nonlocal failed
        if quotes is None:
            failed = True
            return []
        found = []
        for quote in quotes:
            symbol = quote['symbol']
            label = quote.get('longname') or quote.get('shortname') or ''
            if guard and name is not None and name_similarity(name, label) <= MIN_NAME_SIMILARITY:
                continue
            names.setdefault(symbol, label)
            if searches.last_price(symbol) is not None:
                found.append(symbol)
        return found

    candidates = add(searches.search(search), guard=False) if search is not None else []
    own_line = f'{ticker}.{exchange}' if ticker is not None and exchange is not None else None
    if own_line in candidates:
        return Listing(own_line, 'found', 'own line')
    # the ISIN search often returns only some listings: the own line is probed directly; a ticker may belong to
    # another instrument, so an own line with another name is used only once the user accepts it
    if own_line is not None and searches.last_price(own_line) is not None:
        label = searches.name(own_line)
        if name is None or name_similarity(name, label) > MIN_NAME_SIMILARITY:
            return Listing(own_line, 'found', 'own line')
        if own_line == accepted:
            return Listing(own_line, 'found', 'own line (confirmed)')
        if own_line != rejected:
            proposal, proposal_name = own_line, label
    if ticker is not None and not any(on_preferred_exchange(c) for c in candidates):
        # a ticker may belong to another instrument on another exchange: only listings with a similar name
        symbols = [ticker] + [f'{ticker}.{x.code}' for x in Exchanges]
        quotes = searches.search(ticker)
        quotes = [q for q in quotes if q.get('symbol') in symbols] if quotes is not None else None
        by_symbol = set(add(quotes, guard=True)) - set(candidates)
        candidates += sorted(by_symbol)
        if own_line in candidates:
            return Listing(own_line, 'found', 'own line')
    if not candidates:
        return Listing(None, 'failed' if failed else 'not found', None, proposal, proposal_name)
    preferred = [c for c in candidates if on_preferred_exchange(c)]
    pool = preferred or candidates
    rule = 'preferred exchange' if preferred else 'other exchange'
    in_currency = [c for c in pool if currency is not None and searches.currency(c) == currency]
    if in_currency and len(in_currency) < len(pool):
        pool, rule = in_currency, 'currency'
    if len(pool) > 1 and name is not None:
        rule = 'name'
    # the closest name, then the listings of the ticker itself, then the preferred exchange order
    chosen = max(
        pool,
        key=lambda c: (
            name_similarity(name, names[c]) if name is not None else 0.0,
            c.split('.')[0] == ticker,
            -exchange_rank(c),
        ),
    )
    rule = 'symbol search' if chosen in by_symbol else rule
    return Listing(chosen, 'found', rule, proposal, proposal_name)


def store_listing(ticker: str, isin: str | None = None) -> str:
    """Store the history and the information of a listing found by a search; returns its ticker."""
    refresh_history(ticker, full=True)
    refresh_info(ticker, isin=isin)
    return ticker


def search_listing(search: str | None = None, ticker: str | None = None, name: str | None = None) -> str | None:
    """Find the Yahoo Finance listing of an instrument (see find_listing), store its history and information, and
    return its ticker; None if no listing is found.
    """
    listing = find_listing(search=search, ticker=ticker, name=name)
    if listing.ticker is None:
        return None
    isin = search if search is not None and ISIN_PATTERN.fullmatch(search) else None
    return store_listing(listing.ticker, isin=isin)


def adjusted_close(history: pd.DataFrame) -> pd.Series:
    """Close adjusted for distributions (dividends, capital gains), as Yahoo computes it: on each ex-date, the earlier
    closes are scaled by (1 - distribution / previous close). Days without close are left out.
    """
    close = history['close']
    distribution = history['dividend'].fillna(0.0) + history['capital_gain'].fillna(0.0)
    previous_close = close.ffill().shift(1)
    factor = (1.0 - distribution / previous_close).where(distribution > 0, 1.0).fillna(1.0)
    # the factor of an ex-date applies to the days before it
    later_factors = factor[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    return close.mul(later_factors).dropna()


def load_adjusted_closes(tickers: list[str]) -> pd.DataFrame:
    """Stored adjusted closes of listings: date x ticker, in the currency of each listing (see major_currency)."""
    df = query_yahoo_prices(tickers, columns=['close', 'dividend', 'capital_gain'])
    closes = {t: adjusted_close(group.set_index('date')) for t, group in df.groupby('ticker', sort=False)}
    return pd.DataFrame(closes).reindex(columns=[t for t in tickers if t in closes])


def traded_value(tickers: list[str], start: date | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
    """Daily traded value (close x volume) of listings from a day on, each in the currency of the listing (amounts in
    hundredths, e.g. pence, converted): date x ticker, and the currency of each listing (listings without a known
    currency are left out).
    """
    df = query_yahoo_prices(tickers, start=start, columns=['close', 'volume'])
    traded = df.assign(value=df['close'] * df['volume']).pivot(index='date', columns='ticker', values='value')
    currencies = {}
    for ticker, currency in listing_currencies(list(traded.columns)).items():
        currencies[ticker], factor = major_currency(currency)
        traded[ticker] *= factor
    return traded[list(currencies)], currencies


def listing_currencies(tickers: list[str]) -> dict[str, str]:
    """Currency of each listing, as Yahoo reports it (listings without a known currency are left out)."""
    currencies = query_yahoo_instruments(tickers)['currency'].dropna()
    return currencies.to_dict()
