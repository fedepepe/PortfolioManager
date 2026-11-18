"""Yahoo Finance: search, instrument information and price history."""

import time
from enum import Enum
from typing import NamedTuple

import requests
import yfinance as yf
from curl_cffi.requests.exceptions import DNSError, HTTPError, Timeout


class YFinInfoCols(Enum):
    """Instrument information fields of Yahoo Finance."""

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
    """Yahoo Finance exchange: ticker suffix and name."""

    code: str
    name: str | None = None


class Exchanges(Exchange, Enum):
    """Preferred exchanges, in order: an instrument listed on one of them is taken from there (the first one when no
    name tells the listings apart), otherwise from any exchange.
    """

    DE = Exchange('DE', 'Frankfurt')
    L = Exchange('L', 'London')
    MI = Exchange('MI', 'Milan')
    SW = Exchange('SW', 'Switzerland')
    AS = Exchange('AS', 'Amsterdam')
    PA = Exchange('PA', 'Paris')
    BE = Exchange('BE', 'Berlin')
    MU = Exchange('MU', 'Munich')
    DU = Exchange('DU', 'Dusseldorf')
    SG = Exchange('SG', 'Stuttgart')
    XC = Exchange('XC')
    XD = Exchange('XD')


YAHOO_FINANCE_HOST = 'query1.finance.yahoo.com'


def is_yahoo_reachable(timeout: float = 3.0) -> bool:
    """Quick connectivity check, to avoid long retries and timeouts per instrument when working offline. An HTTPS
    request, since a plain TCP connection may be accepted locally (e.g. by a VPN or firewall); any HTTP response
    means the server is reachable.
    """
    try:
        requests.head(f'https://{YAHOO_FINANCE_HOST}', timeout=timeout)
        return True
    except requests.RequestException:
        return False


def search_ticker(ticker: str) -> yf.search.Search:
    """Yahoo Finance search (any text: ticker, ISIN, name), None if Yahoo cannot be reached."""
    for _ in range(5):
        try:
            return yf.Search(query=ticker, include_research=True)
        except (HTTPError, DNSError, Timeout):
            time.sleep(0.5)


def get_ticker_info(ticker: str) -> yf.Ticker.info:
    """Instrument information of a ticker, None if Yahoo cannot be reached."""
    for _ in range(5):
        try:
            return yf.Ticker(ticker).info
        except (HTTPError, DNSError, Timeout):
            time.sleep(0.5)
