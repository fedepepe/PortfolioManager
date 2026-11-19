"""Interest rates of the Tactical Asset Allocation research (table taa_rate_series), downloaded from the central banks
(ECB, SNB, Bank of England, BIS: public data, no account) and Yahoo Finance (US Treasury yields), stored as decimals.

A series without stored values is downloaded whole; later downloads fetch the days from a few before the last stored
one (corrections and late publications overwrite the stored values). The SNB serves whole tables only: each is
downloaded once per update.
"""

import io
import logging
from collections import Counter
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf

from portfolio_manager.config.taa import RateSeries, RateSources
from portfolio_manager.storage.queries import query_interest_rate_last_dates, query_rate_series, save_interest_rates

logger = logging.getLogger(__name__)

TIMEOUT = 30  # seconds
HEADERS = {'User-Agent': 'Mozilla/5.0'}  # the SNB and the Bank of England refuse requests without a browser agent
OVERLAP = timedelta(days=10)  # stored days downloaded again
ECB_URL = 'https://data-api.ecb.europa.eu/service/data/{code}'
BIS_URL = 'https://stats.bis.org/api/v1/data/{code}'
SNB_URL = 'https://data.snb.ch/api/cube/{cube}/data/csv/en'
BOE_URL = 'https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp'
BOE_FIRST_DAY = date(1975, 1, 1)


def _get(url: str, params: dict | None = None) -> str:
    response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.content.decode('utf-8-sig')  # without the byte order mark of the SNB files


def sdmx_rates(url: str, csv_format: str, start: date | None = None) -> pd.Series:
    """A series of an SDMX data portal (ECB, BIS) as CSV, in percent by date, from a day on (all without)."""
    params = {'format': csv_format}
    if start is not None:
        params['startPeriod'] = start.isoformat()
    try:
        text = _get(url, params)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:  # no value from that day on
            return pd.Series(dtype=float)
        raise
    if not text.strip():  # no value from that day on (e.g. a discontinued series)
        return pd.Series(dtype=float)
    df = pd.read_csv(io.StringIO(text), usecols=['TIME_PERIOD', 'OBS_VALUE'])
    return pd.Series(df['OBS_VALUE'].to_numpy(float), index=pd.to_datetime(df['TIME_PERIOD']))


def ecb_rates(code: str, start: date | None = None) -> pd.Series:
    """A series of the ECB data portal ('dataflow/series key'), in percent by date, from a day on (all without)."""
    return sdmx_rates(ECB_URL.format(code=code), 'csvdata', start)


def bis_rates(code: str, start: date | None = None) -> pd.Series:
    """A series of the BIS data portal ('dataflow/series key'), in percent by date, from a day on (all without)."""
    return sdmx_rates(BIS_URL.format(code=code), 'csv', start)


def snb_table(cube: str) -> pd.DataFrame:
    """A table of the SNB data portal: date x item, in percent; an item with several dimensions is named by their values
    joined with '/' (e.g. 'CHF/10J'); monthly dates at the end of their month.
    """
    text = _get(SNB_URL.format(cube=cube))
    lines = text.splitlines()
    header = next(n for n, line in enumerate(lines) if line.startswith('"Date"'))  # after the publication details
    df = pd.read_csv(io.StringIO('\n'.join(lines[header:])), sep=';', dtype=str).dropna(subset=['Value'])
    items = df[df.columns[1:-1]].agg('/'.join, axis=1)
    monthly = df['Date'].str.len() == 7  # YYYY-MM
    dates = pd.to_datetime(df['Date'], format='mixed')
    dates = dates.where(~monthly, dates + pd.offsets.MonthEnd(0))
    values = pd.DataFrame({'date': dates, 'item': items, 'value': df['Value'].astype(float)})
    return values.pivot(index='date', columns='item', values='value').sort_index()


def boe_rates(code: str, start: date | None = None) -> pd.Series:
    """A series of the Bank of England database, in percent by date, from a day on (all without)."""
    params = {
        'csv.x': 'yes',
        'Datefrom': (start or BOE_FIRST_DAY).strftime('%d/%b/%Y'),
        'Dateto': 'now',
        'SeriesCodes': code,
        'CSVF': 'TN',
        'UsingCodes': 'Y',
        'VPD': 'Y',
        'VFD': 'N',
    }
    text = _get(BOE_URL, params)
    if not text.startswith('DATE,'):  # an error page instead of the data
        raise ValueError(f'Bank of England: no data for {code}')
    df = pd.read_csv(io.StringIO(text))
    return pd.Series(df[code].to_numpy(float), index=pd.to_datetime(df['DATE'], format='%d %b %Y'))


def yahoo_rates(ticker: str, start: date | None = None) -> pd.Series:
    """The closes of a Yahoo Finance yield index (e.g. ^TNX), in percent by date, from a day on (all without)."""
    period = {'start': start} if start is not None else {'period': 'max'}
    df = yf.Ticker(ticker).history(interval='1d', auto_adjust=False, **period)
    if df.empty:
        return pd.Series(dtype=float)
    index = pd.DatetimeIndex(df.index)
    index = (index.tz_localize(None) if index.tz is not None else index).normalize()
    return pd.Series(df['Close'].to_numpy(float), index=index)


def download_rates(series: RateSeries, start: date | None = None, snb_tables: dict | None = None) -> pd.Series:
    """Values of a series from a day on (all without), as decimals by date. snb_tables keeps the SNB tables downloaded
    during an update (by cube).
    """
    if series.source == RateSources.ECB:
        percent = ecb_rates(series.code, start)
    elif series.source == RateSources.BIS:
        percent = bis_rates(series.code, start)
    elif series.source == RateSources.BOE:
        percent = boe_rates(series.code, start)
    elif series.source == RateSources.YAHOO:
        percent = yahoo_rates(series.code, start)
    elif series.source == RateSources.SNB:
        cube, item = series.code.split('/', 1)
        snb_tables = {} if snb_tables is None else snb_tables
        if cube not in snb_tables:
            snb_tables[cube] = snb_table(cube)
        table = snb_tables[cube]
        percent = table[item] if item in table.columns else pd.Series(dtype=float)
        if start is not None:
            percent = percent[percent.index >= pd.Timestamp(start)]
    else:
        raise ValueError(f'Unknown source of interest rates: {series.source}')
    percent = percent.dropna()
    return (percent[~percent.index.duplicated(keep='last')] / 100.0).sort_index()


def update_rates(series_list: list[RateSeries] | None = None, full: bool = False) -> Counter:
    """Bring the stored interest rates up to date (the series in use by default; the whole history if none is
    stored or if full); returns the count of each result: 'new' (whole history stored), 'updated' (new days),
    'unchanged', 'failed' (a download problem: the stored values are kept).
    """
    last_dates = query_interest_rate_last_dates()
    snb_tables: dict[str, pd.DataFrame] = {}
    results = Counter()
    for series in series_list or query_rate_series():
        last = last_dates.get(series.key)
        start = None if last is None or full else last - OVERLAP
        try:
            values = download_rates(series, start, snb_tables)
        except Exception as e:  # any download problem: tried again later
            logger.warning('Interest rates %s (%s) not downloaded: %s', series.key, series.source, e)
            results['failed'] += 1
            continue
        save_interest_rates(series.key, values)
        if last is None:
            results['new' if not values.empty else 'failed'] += 1
        else:
            results['updated' if (values.index > pd.Timestamp(last)).any() else 'unchanged'] += 1
    logger.info('Interest rates downloaded: %s', ', '.join(f'{k} {v}' for k, v in sorted(results.items())))
    return results
