"""Members of the stock indices of the Tactical Asset Allocation research (S&P 500, SMI, Euro Stoxx 50), from the tables
of their Wikipedia pages: Yahoo Finance ticker, name and sector of each stock.

Only today's members are listed: a backtest over them leaves out the companies that left the index (survivorship bias,
flattering long-only results). Former members are kept in the database once seen in a list (index_members.current).

For the S&P 500, Wikipedia also lists the changes of the index ("Historical components of the S&P 500": each stock added
and removed, by effective date), complete enough from CHANGES_RELIABLE_FROM: with them the members of each date can be
rebuilt (research.survivorship), and the stocks removed since then are downloaded too, where Yahoo Finance still has
them (companies taken over or bankrupt are usually gone; demoted ones still trade).
"""

import logging
from collections.abc import Callable
from datetime import date
from typing import NamedTuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

from portfolio_manager.market_data.history import refresh_history, refresh_info
from portfolio_manager.storage.queries import (
    query_index_changes,
    query_index_members,
    query_last_close_dates,
    save_index_changes,
    save_index_members,
)

logger = logging.getLogger(__name__)

WIKIPEDIA_URL = 'https://en.wikipedia.org/wiki/{page}'
HEADERS = {'User-Agent': 'PortfolioManager/1.0 (personal research tool)'}  # Wikipedia asks for a descriptive agent
TIMEOUT = 30
CHANGES_PAGES = {'S&P 500': 'Historical_components_of_the_S%26P_500'}  # indices with a table of changes (id changes)
CHANGES_RELIABLE_FROM = pd.Timestamp('2011-01-01')  # earlier, the S&P 500 table misses most changes
# equal-weight S&P 500 of the members of each date (Invesco S&P 500 Equal Weight ETF): measures the bias left
EQUAL_WEIGHT_REFERENCE = {'S&P 500': 'RSP'}


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


def table_rows(table) -> list[list[str]]:
    """Text of the cells of an HTML table below its two header rows, with the cells spanning several rows repeated."""
    rows, spanning = [], {}  # column -> (text, rows left)
    for tr in table.find_all('tr')[2:]:
        cells, row, column = iter(tr.find_all(['th', 'td'])), [], 0
        while True:
            if column in spanning:
                text, left = spanning[column]
                row.append(text)
                spanning[column] = (text, left - 1)
                if left == 1:
                    del spanning[column]
            else:
                cell = next(cells, None)
                if cell is None:
                    break
                text = cell.get_text(' ', strip=True)
                if int(cell.get('rowspan', 1)) > 1:
                    spanning[column] = (text, int(cell['rowspan']) - 1)
                row.append(text)
            column += 1
        rows.append(row)
    return rows


def changes_table(index: StockIndex, html: str) -> pd.DataFrame:
    """Changes of an index from its Wikipedia table (effective date, ticker and name added, removed, reason), the
    tickers as Yahoo Finance tickers (empty: none).
    """
    table = BeautifulSoup(html, 'html.parser').find('table', id='changes')
    if table is None:
        raise ValueError(f'No changes table found on the Wikipedia page of the changes of the {index.name}')
    columns = ['date', 'added', 'added_name', 'removed', 'removed_name', 'reason']
    changes = pd.DataFrame([r[:6] for r in table_rows(table) if len(r) >= 6], columns=columns)
    changes['date'] = pd.to_datetime(changes['date'], format='mixed', errors='coerce')
    for column in ['added', 'removed']:
        tickers = changes[column].str.strip()
        changes[column] = tickers.where(tickers == '', tickers.map(index.to_yahoo))
    return changes.dropna(subset=['date']).reset_index(drop=True)


def refresh_index_changes() -> dict[str, int]:
    """Download and save the changes of the indices with a table of changes; returns their number by index (an index
    whose download fails keeps its stored changes).
    """
    counts = {}
    for index in STOCK_INDICES:
        if index.name not in CHANGES_PAGES:
            continue
        try:
            url = WIKIPEDIA_URL.format(page=CHANGES_PAGES[index.name])
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            changes = changes_table(index, response.text)
        except Exception as e:  # any download or page problem: the stored changes are kept
            logger.warning('Changes of the %s not downloaded: %s', index.name, e)
            continue
        save_index_changes(index.name, changes)
        counts[index.name] = len(changes)
    logger.info('Index changes: %s', ', '.join(f'{name} {n}' for name, n in counts.items()))
    return counts


def former_member_tickers(index_name: str, since: pd.Timestamp = CHANGES_RELIABLE_FROM) -> list[str]:
    """Stocks removed from an index since a day that are not members today."""
    changes = query_index_changes(index_name)
    removed = changes.loc[(changes['date'] >= since) & changes['removed'].notna(), 'removed']
    current = set(query_index_members(index_name)['ticker'])
    return sorted(set(removed) - current)


def download_former_members() -> dict[str, int]:
    """Download the whole history of the former members (see former_member_tickers) without a stored one, and of the
    equal-weight references; returns how many listings were found and not found.
    """
    tickers = [t for name in CHANGES_PAGES for t in former_member_tickers(name)]
    tickers += list(EQUAL_WEIGHT_REFERENCE.values())
    stored = set(query_last_close_dates(tickers).index)  # listings with a stored history
    counts = {'found': 0, 'not found': 0, 'already stored': 0}
    for ticker in tickers:
        if ticker in stored:
            counts['already stored'] += 1
            continue
        try:
            result = refresh_history(ticker)
            if result != 'not found':
                refresh_info(ticker)
        except Exception as e:  # any download problem (most former members are no longer listed)
            logger.debug('%s not downloaded: %s', ticker, e)
            result = 'not found'
        counts['found' if result != 'not found' else 'not found'] += 1
    logger.info('Former index members: %s', ', '.join(f'{k} {v}' for k, v in counts.items()))
    return counts


def member_tickers() -> list[str]:
    """Tickers of today's members of every index."""
    return query_index_members()['ticker'].drop_duplicates().to_list()
