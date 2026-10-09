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


def choose_ticker(tickers: list[str], ticker: str | None = None) -> str:
    """Follow the priority order of exchanges: first {ticker}.{exchange}, then any ticker listed on an exchange."""
    for prefix in ([ticker] if ticker is not None else []) + [None]:
        for x in Exchanges:
            for t in tickers:
                if t.endswith(f'.{x.code}') and (prefix is None or t == f'{prefix}.{x.code}'):
                    return t
    return tickers[0]


def _candidates(search: str | None, ticker: str | None) -> tuple[list[dict], str | None]:
    # listings found by the search text (every result), otherwise by ticker (exact, or with an exchange suffix); and
    # the ISIN if the search text is one
    if search is not None:
        results = search_ticker(search)
        quotes = results.quotes if results is not None else []
        if quotes:
            return quotes, search if ISIN_PATTERN.fullmatch(search) else None
    if ticker is not None:
        results = search_ticker(ticker)
        symbols = [ticker] + [f'{ticker}.{x.code}' for x in Exchanges]
        quotes = [q for q in (results.quotes if results is not None else []) if q.get('symbol') in symbols]
        return quotes, None
    return [], None


def search_listing(search: str | None = None, ticker: str | None = None, name: str | None = None) -> str | None:
    """Find the Yahoo Finance listing of an instrument (by a search text, usually its ISIN, then by its ticker), store
    its history and information, and return its ticker; None if no listing with prices is found. Among several
    listings: the closest name, without a name the preferred exchange.
    """
    search = None if is_missing(search) else search
    ticker = None if is_missing(ticker) else ticker
    quotes, found_isin = _candidates(search, ticker)
    names = {}
    for quote in quotes:
        names.setdefault(quote['symbol'], quote.get('longname') or quote.get('shortname') or '')
    histories = {}
    for symbol in names:
        history = download_history(symbol)
        if not history['close'].dropna().empty:
            histories[symbol] = history
    if not histories:
        return None
    symbols = list(histories)
    if len(symbols) > 1 and not is_missing(name):
        chosen = max(symbols, key=lambda s: SequenceMatcher(None, name, names[s]).ratio())
    else:
        chosen = choose_ticker(symbols, ticker)
    save_yahoo_prices(upsert_yahoo_instrument(chosen), histories[chosen], replace=True)
    refresh_info(chosen, isin=found_isin)
    return chosen


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


def listing_currencies(tickers: list[str]) -> dict[str, str]:
    """Currency of each listing, as Yahoo reports it (listings without a known currency are left out)."""
    currencies = query_yahoo_instruments(tickers)['currency'].dropna()
    return currencies.to_dict()
