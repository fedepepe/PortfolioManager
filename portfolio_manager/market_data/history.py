"""Yahoo Finance daily history stored in the database: listing search, incremental downloads and adjusted prices.

Each listing is searched once (by ISIN, then by ticker) and its history stored; later downloads only fetch the days
since the last stored one, plus a few stored days to check them: if Yahoo changed them (a split, a correction), the
whole history is downloaded again. The adjusted close is computed from the close and the distributions (dividends,
capital gains), as Yahoo computes it; the prices are already adjusted for splits.
"""

import logging
from collections.abc import Callable
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import NamedTuple

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

from portfolio_manager.config.taa import TAA_TICKERS
from portfolio_manager.market_data.yahoo import Exchanges, get_ticker_info, search_ticker
from portfolio_manager.storage.queries import (
    YAHOO_PRICE_COLUMNS,
    query_currency_fixes,
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
MIN_NAME_SIMILARITY = 0.6  # without prices to compare, listings found by ticker must have a more similar name
MAX_PRICE_GAP = 0.02  # median daily gap between the prices of a listing and those of the instrument
# currencies tried for the prices of a listing that does not match in the currency Yahoo reports (besides the currency
# of the instrument): Yahoo reports a wrong one for some listings, e.g. USD prices labelled CHF on SIX
FIX_CURRENCIES = ('EUR', 'USD', 'CHF', 'GBP')
# days on which a listing and the instrument must both have prices to compare them (about a third of the period): a
# listing with fewer (e.g. a single stale quote) gives no usable history, and in other currencies it could match by
# chance
MIN_COMMON_DAYS = 20
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


def history_period(ticker: str) -> str:
    """History downloaded for a listing without a stored one: the whole history for the instruments of the Tactical
    Asset Allocation research (strategies are tested over many years), HISTORY_PERIOD for the others.
    """
    return 'max' if ticker in TAA_TICKERS else HISTORY_PERIOD


def download_history(ticker: str, start: date | None = None) -> pd.DataFrame:
    """Daily history of a listing from a day (without a start: see history_period): YAHOO_PRICE_COLUMNS by date (dates
    of the exchange), corporate actions only on the days they happen; empty if Yahoo has none.
    """
    period = {'start': start} if start is not None else {'period': history_period(ticker)}
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


def refresh_info(ticker: str):
    """Download and save the information of a listing (name, currency, exchange, ...), as Yahoo reports it. No ISIN:
    yfinance's comes from a name search on Business Insider, not from Yahoo, and is often another security's.
    """
    info = get_ticker_info(ticker) or {}
    fields = {INFO_FIELDS[k]: v for k, v in info.items() if k in INFO_FIELDS}
    upsert_yahoo_instrument(ticker, fields)


def on_preferred_exchange(ticker: str) -> bool:
    """Whether a listing is on one of the preferred exchanges (its ticker ends with their suffix)."""
    return any(ticker.endswith(f'.{x.code}') for x in Exchanges)


def name_similarity(name: str, other: str) -> float:
    """Similarity of two instrument names, from 0 to 1 (case ignored)."""
    return SequenceMatcher(None, name.lower(), other.lower()).ratio()


class CurrencyFix(NamedTuple):
    """The currency of the prices of a listing (currency) where Yahoo reports another one (yahoo_currency, None if
    Yahoo reports none), with the median daily gap of the prices in that currency.
    """

    yahoo_currency: str | None
    currency: str
    gap: float


class Listing(NamedTuple):
    """Result of the search of a listing: ticker (None if none), status (found, not found, failed), the rule that chose
    it (own line, preferred exchange, currency, price, name, symbol search, other exchange; for a Degiro product
    not searched: no Degiro prices, no recent Degiro prices; own line (currency fixed): the own line, in another
    currency than Yahoo reports), when nothing is found or the search failed, why (for the log), and the currency
    fix of the listing found if its prices match only in another currency than Yahoo reports.
    """

    ticker: str | None
    status: str
    rule: str | None = None
    detail: str | None = None
    currency_fix: CurrencyFix | None = None


class ListingDownloadError(Exception):
    """The prices of a listing returned by a Yahoo search could not be downloaded (Yahoo did not answer)."""


class PriceReference(NamedTuple):
    """Prices of an instrument to which the prices of its candidate listings are compared: daily closes (date x
    close, in the currency below) and the exchange rate of another currency to it (None if unknown).
    """

    prices: pd.Series
    currency: str
    rate: Callable[[str], pd.Series | None]


class ListingSearches:
    """Searches and price probes of one run, kept so that products of the same fund repeat neither."""

    def __init__(self):
        self.quotes: dict[str, list[dict] | None] = {}
        self.last_prices: dict[str, pd.Timestamp | None] = {}
        self.currencies: dict[str, str | None] = {}
        self.daily_closes: dict[tuple[str, date, date], pd.Series] = {}
        self.fixes: dict[str, CurrencyFix] = {}  # listings found in another currency than Yahoo reports

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

    def closes(self, ticker: str, start: date, end: date, listed: bool = False) -> pd.Series:
        """Daily closes of a listing from a day to another (in the currency of its prices); empty if none. Yahoo
        answers "no timezone" both for a ticker it does not know and when its answer gets lost: tried twice, then
        empty for a guessed ticker, ListingDownloadError for one returned by a Yahoo search (listed). Other download
        errors are raised.
        """
        key = (ticker, start, end)
        if key not in self.daily_closes:
            for attempt in range(2):
                listing = yf.Ticker(ticker)
                try:
                    df = listing.history(
                        start=start, end=end + timedelta(days=1), interval='1d', auto_adjust=False, raise_errors=True
                    )
                    break
                except YFPricesMissingError:  # a known listing without prices on these days
                    df = pd.DataFrame()
                    break
                except YFTzMissingError:
                    df = pd.DataFrame()
                    if attempt == 1 and listed:
                        raise ListingDownloadError(f'no prices downloaded for {ticker}') from None
            closes = df['Close'].dropna() if 'Close' in df else pd.Series(dtype=float)
            index = pd.DatetimeIndex(closes.index)
            closes.index = (index.tz_localize(None) if index.tz is not None else index).normalize()
            self.daily_closes[key] = closes
            self.currencies.setdefault(ticker, (listing.history_metadata or {}).get('currency'))
        return self.daily_closes[key]

    def price_gap(
        self, ticker: str, reference: PriceReference, currency: str | None = None, as_reported: bool = False
    ) -> float | None:
        """Median daily gap between the closes of a listing (converted to the currency of the reference) and the
        reference prices, over the days of both; None without common days or exchange rate. The closes are taken in
        the given currency, otherwise (unless as_reported) in the currency found for them (see currency_fix),
        otherwise in the currency Yahoo reports.
        """
        comparison = self.price_comparison(ticker, reference, currency=currency, as_reported=as_reported)
        return comparison[0] if comparison is not None else None

    def price_comparison(
        self, ticker: str, reference: PriceReference, currency: str | None = None, as_reported: bool = False
    ) -> tuple[float, int] | None:
        """Median daily gap (see price_gap) and number of days compared; None without common days or exchange rate."""
        days = reference.prices.index
        closes = self.closes(ticker, days[0].date(), days[-1].date())
        if currency is None and not as_reported and ticker in self.fixes:
            currency = self.fixes[ticker].currency
        currency = currency or self.currencies.get(ticker)
        if closes.empty or not currency:
            return None
        currency, factor = major_currency(currency)
        closes = closes * factor
        if currency != reference.currency:
            rate = reference.rate(currency)
            if rate is None:
                return None
            rate = rate.dropna()
            closes = closes * rate.reindex(rate.index.union(closes.index)).ffill().reindex(closes.index)
        both = pd.concat([closes, reference.prices], axis=1, join='inner').dropna()
        if both.empty:
            return None
        return float((both.iloc[:, 0] / both.iloc[:, 1] - 1.0).abs().median()), len(both)

    def currency_fix(self, ticker: str, reference: PriceReference) -> CurrencyFix | None:
        """The currency in which the closes of a listing match the reference prices (within MAX_PRICE_GAP, the
        closest, on at least MIN_COMMON_DAYS common days), when they do not in the currency Yahoo reports: the currency
        of the reference or one of FIX_CURRENCIES; None if none matches. Found fixes are kept for the run.
        """
        yahoo_currency = self.currencies.get(ticker)
        reported = major_currency(yahoo_currency)[0] if yahoo_currency else None
        gaps = {}
        for currency in dict.fromkeys((reference.currency, *FIX_CURRENCIES)):
            if currency != reported:
                comparison = self.price_comparison(ticker, reference, currency=currency)
                if comparison is not None and comparison[0] <= MAX_PRICE_GAP and comparison[1] >= MIN_COMMON_DAYS:
                    gaps[currency] = comparison[0]
        if not gaps:
            return None
        currency = min(gaps, key=gaps.get)
        self.fixes[ticker] = CurrencyFix(yahoo_currency, currency, gaps[currency])
        return self.fixes[ticker]

    def currency(self, ticker: str) -> str | None:
        """Currency of the prices of a listing (pence counted as pounds, see major_currency): the one found for them
        in this run (see currency_fix), otherwise as Yahoo reports it with them.
        """
        if ticker in self.fixes:
            return self.fixes[ticker].currency
        if ticker not in self.currencies:
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
    reference: PriceReference | None = None,
    searches: ListingSearches | None = None,
) -> Listing:
    """Find the Yahoo Finance listing of an instrument (nothing is stored). The candidates are the listings found by the
    search text (usually the ISIN) and the ticker on its own exchange (the Yahoo suffix of the exchange of the
    instrument, e.g. EUNL.DE for EUNL on Xetra, probed directly when the search misses it); when none is on a preferred
    exchange, also all the listings found by searching the ticker.

    With the prices of the instrument (reference), a candidate must have prices on at least MIN_COMMON_DAYS of its days,
    within MAX_PRICE_GAP of them (median daily gap, see ListingSearches.price_gap), in the currency Yahoo reports or
    another one (see ListingSearches.currency_fix). Without them, a
    candidate must have prices, and the own line and the listings of the ticker a name similar to the given one.

    The choice: the own line; otherwise the listings on the preferred exchanges if any, among them those in the currency
    of the instrument if any, then the closest prices (or name), then the listings whose symbol is the ticker, then
    the preferred exchange order.
    """
    search, ticker, name = (None if is_missing(v) else v for v in (search, ticker, name))
    searches = searches or ListingSearches()
    names, by_symbol, failed = {}, set(), False
    rejected: dict[str, str] = {}  # why each candidate was rejected (for the log when nothing is found)

    def has_prices(symbol: str) -> bool:
        if reference is None:
            found = searches.last_price(symbol) is not None
        else:
            days = reference.prices.index
            found = not searches.closes(symbol, days[0].date(), days[-1].date(), listed=symbol in names).empty
        if not found:
            rejected[symbol] = 'no prices'
        return found

    def matches(symbol: str) -> bool:
        # the listing is the instrument: the same prices or (without prices to compare) a similar name; no ISIN check:
        # yfinance's ISIN comes from a name search on Business Insider, often of another security
        if reference is None:
            similar = name is None or name_similarity(name, names.get(symbol, '')) > MIN_NAME_SIMILARITY
            if not similar:
                rejected[symbol] = 'other name'
            return similar
        comparison = searches.price_comparison(symbol, reference, as_reported=True)
        if comparison is not None and comparison[1] < MIN_COMMON_DAYS:
            rejected[symbol] = f'only {comparison[1]} common days'
            return False
        if comparison is not None and comparison[0] <= MAX_PRICE_GAP:
            searches.fixes.pop(symbol, None)  # matching in the currency Yahoo reports: no fix
            return True
        # Yahoo may report a wrong currency for the prices: they match in another one
        if searches.currency_fix(symbol, reference) is not None:
            return True
        rejected[symbol] = 'no common days' if comparison is None else f'gap {comparison[0]:.1%}'
        return False

    def found(symbol: str, rule: str) -> Listing:
        # a listing found, with the currency of its prices if Yahoo reports another one
        fix = searches.fixes.get(symbol) if reference is not None else None
        if symbol == own_line and fix is not None:
            rule = 'own line (currency fixed)'
        return Listing(symbol, 'found', rule, currency_fix=fix)

    def add(quotes: list[dict] | None, guard: bool) -> list[str]:
        # the listings with prices among the quotes (checked if guarded, always with a reference)
        nonlocal failed
        if quotes is None:
            failed = True
            return []
        found = []
        for quote in quotes:
            symbol = quote['symbol']
            names.setdefault(symbol, quote.get('longname') or quote.get('shortname') or '')
            if has_prices(symbol) and (not (guard or reference is not None) or matches(symbol)):
                found.append(symbol)
        return found

    own_line = f'{ticker}.{exchange}' if ticker is not None and exchange is not None else None
    candidates = add(searches.search(search), guard=False) if search is not None else []
    if own_line in candidates:
        return found(own_line, 'own line')
    # the search often returns only some listings: the own line is probed directly; a ticker may belong to another
    # instrument, so it must pass the check
    if own_line is not None and own_line not in names and has_prices(own_line):
        if reference is None:
            names[own_line] = searches.name(own_line)
        if matches(own_line):
            return found(own_line, 'own line')
    if ticker is not None and not any(on_preferred_exchange(c) for c in candidates):
        # every listing found by the ticker (Yahoo symbols may differ from it, e.g. WMEGA.SW for WMEG) is a
        # candidate; it may belong to another instrument, so it must pass the check
        by_symbol = set(add(searches.search(ticker), guard=True)) - set(candidates)
        candidates += sorted(by_symbol)
        if own_line in candidates:
            return found(own_line, 'own line')
    if not candidates:
        if failed:
            return Listing(None, 'failed', detail='Yahoo search did not answer')
        detail = ', '.join(f'{s} {why}' for s, why in rejected.items()) or 'no listing'
        return Listing(None, 'not found', detail=detail)
    preferred = [c for c in candidates if on_preferred_exchange(c)]
    pool = preferred or candidates
    rule = 'preferred exchange' if preferred else 'other exchange'
    in_currency = [c for c in pool if currency is not None and searches.currency(c) == currency]
    if in_currency and len(in_currency) < len(pool):
        pool, rule = in_currency, 'currency'
    if len(pool) > 1 and reference is not None:
        rule = 'price'
    elif len(pool) > 1 and name is not None:
        rule = 'name'

    def closeness(c: str) -> float:
        if reference is not None:
            return -searches.price_gap(c, reference)
        return name_similarity(name, names[c]) if name is not None else 0.0

    # the closest prices (or name), then the listings whose symbol is the ticker, then the preferred exchange order
    chosen = max(pool, key=lambda c: (closeness(c), c.split('.')[0] == ticker, -exchange_rank(c)))
    return found(chosen, 'symbol search' if chosen in by_symbol else rule)


def store_listing(ticker: str) -> str:
    """Store the history and the information of a listing found by a search; returns its ticker."""
    refresh_history(ticker, full=True)
    refresh_info(ticker)
    return ticker


def search_listing(search: str | None = None, ticker: str | None = None, name: str | None = None) -> str | None:
    """Find the Yahoo Finance listing of an instrument (see find_listing), store its history and information, and
    return its ticker; None if no listing is found.
    """
    listing = find_listing(search=search, ticker=ticker, name=name)
    if listing.ticker is None:
        return None
    return store_listing(listing.ticker)


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
    """Currency of the prices of each listing: as Yahoo reports it, unless the mapping found that they are in another
    one (yahoo_currency_fixes); listings without a known currency are left out.
    """
    currencies = query_yahoo_instruments(tickers)['currency'].dropna().to_dict()
    currencies.update(query_currency_fixes(tickers)['currency'].to_dict())
    return currencies
