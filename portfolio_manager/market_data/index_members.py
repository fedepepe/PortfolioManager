"""Members of the stock indices of the Tactical Asset Allocation research (S&P 500, SMI, Euro Stoxx 50), from the tables
of their Wikipedia pages: Yahoo Finance ticker, name and sector of each stock.

Only today's members are listed: a backtest over them leaves out the companies that left the index (survivorship bias,
flattering long-only results). Former members are kept in the database once seen in a list (index_members.current).
"""

import logging
from collections.abc import Callable
from datetime import date
from typing import NamedTuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

from portfolio_manager.storage.queries import query_index_members, save_index_members

logger = logging.getLogger(__name__)

WIKIPEDIA_URL = 'https://en.wikipedia.org/wiki/{page}'
HEADERS = {'User-Agent': 'PortfolioManager/1.0 (personal research tool)'}  # Wikipedia asks for a descriptive agent
TIMEOUT = 30


class StockIndex(NamedTuple):
    """An index: its Wikipedia page, the columns of the members table (ticker, name, sector) and how its tickers become
    Yahoo Finance tickers.
    """

    name: str
    page: str
    ticker_column: str
    name_column: str
    sector_column: str
    to_yahoo: Callable[[str], str]


STOCK_INDICES = [
    StockIndex(
        'S&P 500', 'List_of_S%26P_500_companies', 'Symbol', 'Security', 'GICS Sector', lambda t: t.replace('.', '-')
    ),
    StockIndex('SMI', 'Swiss_Market_Index', 'Ticker', 'Name', 'Sector', lambda t: f'{t}.SW'),
    StockIndex('Euro Stoxx 50', 'EURO_STOXX_50', 'Ticker', 'Name', 'Sector', lambda t: t),
]


def html_tables(html: str) -> list[pd.DataFrame]:
    """The tables of a Wikipedia page (class wikitable) as text, the first row as header."""
    tables = []
    for table in BeautifulSoup(html, 'html.parser').find_all('table', class_='wikitable'):
        rows = [[cell.get_text(' ', strip=True) for cell in row.find_all(['th', 'td'])] for row in table.find_all('tr')]
        if len(rows) < 2:
            continue
        width = len(rows[0])
        tables.append(pd.DataFrame([r[:width] for r in rows[1:] if len(r) >= width], columns=rows[0]))
    return tables


def members_table(index: StockIndex, html: str) -> pd.DataFrame:
    """Members of an index from its page: ticker (Yahoo Finance), name and sector."""
    columns = [index.ticker_column, index.name_column, index.sector_column]
    for table in html_tables(html):
        if set(columns) <= set(table.columns):
            members = table[columns].set_axis(['ticker', 'name', 'sector'], axis=1)
            members = members[members['ticker'].str.strip() != '']
            members['ticker'] = members['ticker'].str.strip().map(index.to_yahoo)
            return members.drop_duplicates('ticker').reset_index(drop=True)
    raise ValueError(f'No members table found on the Wikipedia page of the {index.name}')


def download_members(index: StockIndex) -> pd.DataFrame:
    """Download the members of an index from Wikipedia."""
    response = requests.get(WIKIPEDIA_URL.format(page=index.page), headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return members_table(index, response.text)


def refresh_index_members(today: date | None = None) -> dict[str, int]:
    """Download and save the members of every index; returns the number of members of each (an index whose download
    fails keeps its stored list).
    """
    counts = {}
    for index in STOCK_INDICES:
        try:
            members = download_members(index)
        except Exception as e:  # any download or page problem: the stored list is kept
            logger.warning('Members of the %s not downloaded: %s', index.name, e)
            continue
        save_index_members(index.name, members, today or date.today())
        counts[index.name] = len(members)
    logger.info('Index members: %s', ', '.join(f'{name} {n}' for name, n in counts.items()))
    return counts


def member_tickers() -> list[str]:
    """Tickers of today's members of every index."""
    return query_index_members()['ticker'].drop_duplicates().to_list()
