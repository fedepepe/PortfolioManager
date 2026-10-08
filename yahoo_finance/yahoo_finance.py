import time
from enum import Enum
from typing import NamedTuple

import pandas as pd
import requests
import yfinance as yf
from curl_cffi.requests.exceptions import DNSError, HTTPError, Timeout


class YFinHistCols(Enum):
    open = 'Open'
    high = 'High'
    low = 'Low'
    close = 'Close'
    adj_close = 'Adj Close'
    volume = 'Volume'
    dividends = 'Dividends'
    stock_splits = 'Stock Splits'
    capital_gains = 'Capital Gains'

    def __str__(self):
        return self.value

    @classmethod
    def get_entry_by_val(cls, value: str):
        lst = [e for e in YFinHistCols if e.value == value]
        return lst[0] if lst else value


YF_PROD_INFO_LABEL = 'Product Info'


class YFinInfoCols(Enum):
    symbol = 'symbol'
    currency = 'currency'
    isin = 'isin'
    name_short = 'shortName'
    name_long = 'longName'
    quote_type = 'quoteType'
    exchange = 'exchange'
    market = 'market'
    region = 'region'


class Exchange(NamedTuple):
    code: str
    name: str | None = None


class Exchanges(Exchange, Enum):
    DE = Exchange('DE', 'Frankfurt')
    L = Exchange('L', 'London')
    MI = Exchange('MI', 'Milan')
    SW = Exchange('SW', 'Switzerland')
    BE = Exchange('BE', 'Berlin')
    MU = Exchange('MU', 'Munich')
    DU = Exchange('DU', 'Dusseldorf')
    SG = Exchange('SG', 'Singapore')
    XC = Exchange('XC')
    XD = Exchange('XD')


YAHOO_FINANCE_HOST = 'query1.finance.yahoo.com'


def is_yahoo_reachable(timeout: float = 3.0) -> bool:
    # quick connectivity check, to avoid long retries and timeouts per instrument when working offline.
    # An HTTPS request, since a plain TCP connection may be accepted locally (e.g. by a VPN or firewall);
    # any HTTP response means the server is reachable
    try:
        requests.head(f'https://{YAHOO_FINANCE_HOST}', timeout=timeout)
        return True
    except requests.RequestException:
        return False


def search_ticker(ticker: str) -> yf.search.Search:
    for _ in range(5):
        try:
            return yf.Search(query=ticker, include_research=True)
        except (HTTPError, DNSError, Timeout):
            time.sleep(0.5)


def get_ticker_info(ticker: str) -> yf.Ticker.info:
    for _ in range(5):
        try:
            return yf.Ticker(ticker).info
        except (HTTPError, DNSError, Timeout):
            time.sleep(0.5)


def fetch_history_single(ticker: str | yf.Ticker):
    if isinstance(ticker, str):
        ticker = yf.Ticker(ticker=ticker)
    elif isinstance(ticker, yf.Ticker):
        pass
    else:
        raise TypeError
    df = ticker.history(period='10y', interval='1d', auto_adjust=False)
    df.index = pd.to_datetime(df.index)
    df = df.resample('D').last().dropna(how='all')
    df.index = df.index.tz_localize(None)
    return df


def fetch_history(
    tickers: str | list[str] | yf.Ticker | list[yf.Ticker],
    columns: str | YFinHistCols | list[str] | list[YFinHistCols] = YFinHistCols.adj_close,
) -> dict[str | YFinHistCols, pd.DataFrame]:
    if isinstance(tickers, str) or isinstance(tickers, yf.Ticker):
        tickers = [tickers]
    if isinstance(columns, str) or isinstance(columns, YFinHistCols):
        columns = [columns]
    tickers = [yf.Ticker(t) if isinstance(t, str) else t for t in tickers]
    data = {col: pd.DataFrame() for col in columns}
    for ticker in tickers:
        df = fetch_history_single(ticker=ticker)
        for col in columns:
            if isinstance(col, str):
                data[col] = pd.concat([data[col], df[col].rename(ticker.ticker)], axis=1)
            elif isinstance(col, YFinHistCols):
                data[col] = pd.concat([data[col], df[col.value].rename(ticker.ticker)], axis=1)
    for col in columns:
        data[col].index = pd.to_datetime(data[col].index)
        data[col] = data[col].sort_index()
    return data


def search_fetch_history(
    ticker: str | None = None,
    isin: str | None = None,
    columns: str | YFinHistCols | list[str] | list[YFinHistCols] = YFinHistCols.adj_close,
) -> dict[str | YFinHistCols, pd.DataFrame] | None:
    if ticker is None and isin is None:
        raise ValueError('Ticker and ISIN both missing. At least one must be given.')
    search = search_ticker(ticker=ticker if ticker is not None else isin)
    if search is None:
        # Yahoo Finance not reachable after several attempts
        return None
    if ticker is not None:
        match = [
            e
            for e in search.all['quotes']
            if e['symbol'] == ticker or e['symbol'] in [f'{ticker}.{x.code}' for x in Exchanges]
        ]
    else:
        match = search.all['quotes']
    if isinstance(columns, str) or isinstance(columns, YFinHistCols):
        columns = [columns]
    columns_ext = columns + [YF_PROD_INFO_LABEL]
    data = {col: pd.DataFrame() for col in columns_ext}
    for t in match:
        ticker = t['symbol']
        data_ticker = fetch_history(tickers=ticker, columns=columns)
        for col in data_ticker:
            data_ticker[col] = data_ticker[col].dropna(axis=1, how='all')
        yf_info = get_ticker_info(ticker) or {}
        data_ticker[YF_PROD_INFO_LABEL] = pd.DataFrame(columns=[ticker])
        for field in YFinInfoCols:
            data_ticker[YF_PROD_INFO_LABEL].loc[field.value] = yf_info.get(field.value, '')
        if isin is not None:
            data_ticker[YF_PROD_INFO_LABEL].loc[YFinInfoCols.isin.value] = isin
        else:
            data_ticker[YF_PROD_INFO_LABEL].loc[YFinInfoCols.isin.value] = yf.Ticker(ticker).isin
        for key in data_ticker:
            data[key] = pd.concat([data[key], data_ticker[key]], axis=1)
    return data
