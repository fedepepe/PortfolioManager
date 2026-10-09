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


def on_preferred_exchange(ticker: str) -> bool:
    """Whether a listing is on one of the preferred exchanges (its ticker ends with their suffix)."""
    return any(ticker.endswith(f'.{x.code}') for x in Exchanges)


def choose_ticker(tickers: list[str], ticker: str | None = None) -> str:
    """Follow the priority order of exchanges: first {ticker}.{exchange}, then any ticker listed on an exchange."""
    for prefix in ([ticker] if ticker is not None else []) + [None]:
        for x in Exchanges:
            for t in tickers:
                if t.endswith(f'.{x.code}') and (prefix is None or t == f'{prefix}.{x.code}'):
                    return t
    return tickers[0]


def _search_quotes(search: str) -> list[dict]:
    # every listing found by a search text (an ISIN, a name, ...)
    results = search_ticker(search)
    return results.quotes if results is not None else []


def _ticker_quotes(ticker: str) -> list[dict]:
    # the listings of a ticker: exact, or with the suffix of a preferred exchange
    symbols = [ticker] + [f'{ticker}.{x.code}' for x in Exchanges]
    return [q for q in _search_quotes(ticker) if q.get('symbol') in symbols]


def _add_candidates(quotes: list[dict], names: dict[str, str], histories: dict[str, pd.DataFrame]):
    # the listings not seen yet, with their name; those with prices get their history (in place)
    for quote in quotes:
        symbol = quote['symbol']
        if symbol in names:
            continue
        names[symbol] = quote.get('longname') or quote.get('shortname') or ''
        history = download_history(symbol)
        if not history['close'].dropna().empty:
            histories[symbol] = history


def search_listing(search: str | None = None, ticker: str | None = None, name: str | None = None) -> str | None:
    """Find the Yahoo Finance listing of an instrument, store its history and information, and return its ticker;
    None if no listing with prices is found. The candidates are the listings found by the search text (usually the
    ISIN), plus those of the ticker when the search finds none with prices on a preferred exchange. Among several:
    those on the preferred exchanges if there are any, then the closest name (without a name, the first preferred
    exchange).
    """
    search = None if is_missing(search) else search
    ticker = None if is_missing(ticker) else ticker
    names, histories = {}, {}
    if search is not None:
        _add_candidates(_search_quotes(search), names, histories)
    if ticker is not None and not any(on_preferred_exchange(s) for s in histories):
        _add_candidates(_ticker_quotes(ticker), names, histories)
    if not histories:
        return None
    found_isin = search if search is not None and ISIN_PATTERN.fullmatch(search) else None
    # the listings on the preferred exchanges, if any: a closer name on another exchange (e.g. Tokyo) does not win
    symbols = [s for s in histories if on_preferred_exchange(s)] or list(histories)
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
