"""Degiro price charts of the portfolio products and of the exchange rates."""

import logging
from enum import Enum

import pandas as pd
from degiro_connector.quotecast.models.chart import ChartRequest, Interval
from degiro_connector.quotecast.tools.chart_fetcher import ChartFetcher, SeriesFormatter
from degiro_connector.trading.api import API

from portfolio_manager.config.accounts import Account
from portfolio_manager.config.settings import DATA_DIR, FX_RATES_CHART_FILE_NAME, PRODUCTS_CHART_FILE_NAME
from portfolio_manager.degiro.connection import get_degiro_connection
from portfolio_manager.degiro.products import fetch_product_info
from portfolio_manager.storage.files import save_df_to_excel
from portfolio_manager.storage.queries import (
    insert_degiro_hist,
    query_account_product_ids,
    query_currency_pairs,
    query_degiro_hist,
)

logger = logging.getLogger(__name__)


class ChartType(str, Enum):
    """Chart series available from Degiro."""

    PRICE = 'price'
    OHLC = 'ohlc'
    VOLUME = 'volume'


def fetch_hist_data_single(
    chart_fetcher: ChartFetcher,
    vwd_id: int,
    period: Interval = Interval.P10Y,
    resolution: Interval = Interval.P1D,
) -> pd.DataFrame:
    """Fetch price, ohlc and volume series of one product in a single request."""
    chart_request = ChartRequest(
        culture='en-US',
        period=period,
        requestid='1',
        resolution=resolution,
        series=[f'{chart_type.value}:issueid:{int(vwd_id)}' for chart_type in ChartType],
        tz='Europe/Paris',
    )
    data = chart_fetcher.get_chart(chart_request=chart_request, raw=False)
    hist_df = pd.DataFrame()
    if data is None:
        return hist_df
    for series in data.series:
        # some products return empty series, which the formatter cannot parse
        if series.times is None or not series.data or not SeriesFormatter.is_timeseries(series=series):
            continue
        df = SeriesFormatter.format(series=series).to_pandas().set_index('timestamp')
        hist_df = pd.concat([hist_df, df], axis=1)
    if not hist_df.empty:
        hist_df.index = pd.to_datetime(hist_df.index).normalize()
    return hist_df


def fetch_charts(
    degiro_conn: API | None = None,
    product_ids: int | list[int] | None = None,
    product_info_df: pd.DataFrame | None = None,
    chart_type: ChartType = ChartType.PRICE,
    period: Interval | None = Interval.P10Y,
    resolution: Interval | None = Interval.P1D,
    return_df: bool = True,
) -> pd.DataFrame:
    """All historical data is stored into the database; the chart_type column is returned if return_df is True."""
    if degiro_conn is None:
        degiro_conn = get_degiro_connection()
    if product_info_df is None:
        if isinstance(product_ids, int):
            product_ids = [product_ids]
        # GET PRODUCT INFO
        product_info_df = fetch_product_info(degiro_conn=degiro_conn, product_ids=product_ids)
    else:
        product_ids = product_info_df['id'].astype(int).to_list()
    # ESTABLISH CONNECTION
    client_details_table = degiro_conn.get_client_details()
    # int_account = client_details_table['data']['intAccount']
    user_token = client_details_table['data']['id']
    # FETCH DATA
    chart_fetcher = ChartFetcher(user_token=user_token)
    chart_df = pd.DataFrame()
    if not all([s == 'issueid' for s in product_info_df['vwd_identifier_type'].to_list()]):
        for prod_id in product_info_df.index:
            if product_info_df.loc[prod_id, 'vwd_identifier_type'] != 'issueid':
                vwd_id_found = product_info_df.loc[
                    (product_info_df['name'] == product_info_df.loc[prod_id, 'name'])
                    & (product_info_df['vwd_identifier_type'] == 'issueid'),
                    'vwd_id',
                ]
                if not vwd_id_found.empty:
                    product_info_df.loc[prod_id, 'vwd_id'] = int(vwd_id_found.iloc[0])
    # restrict product_info_df to entries with a valid vwd_id
    product_info_df = product_info_df[product_info_df['vwd_id'].notna()]
    product_ids = product_info_df['id'].astype(int).to_list()
    vwd_ids = [product_info_df.loc[prod_id, 'vwd_id'] for prod_id in product_ids]
    for vwd_id, product_id in zip(vwd_ids, product_ids, strict=True):
        hist_df = fetch_hist_data_single(
            chart_fetcher=chart_fetcher, vwd_id=vwd_id, period=period, resolution=resolution
        )
        if hist_df.empty:
            continue
        insert_degiro_hist(product_id=product_id, df=hist_df)
        if return_df and chart_type.value in hist_df:
            chart_df = pd.concat([chart_df, hist_df[chart_type.value].rename(product_id)], axis=1)
    return chart_df.sort_index()


def charts_file_name(account: Account, chart_name: str, chart_type: ChartType = ChartType.PRICE) -> str:
    """Excel copy of the saved charts of an account."""
    return f'{account.name}_{chart_name}_{chart_type.value}'


def _chart_product_ids(account: Account, chart_name: str) -> list[int]:
    # products of the account, or the currency pairs (exchange rates)
    if chart_name == FX_RATES_CHART_FILE_NAME:
        return sorted(query_currency_pairs().values())
    return query_account_product_ids(account.name)


def fetch_portfolio_charts(
    account: Account, degiro_conn: API | None = None, chart_name: str = PRODUCTS_CHART_FILE_NAME
):
    """Download the price charts of the products of an account (or of the exchange rates) into the database, with an
    Excel copy.
    """
    fetch_charts(degiro_conn=degiro_conn, product_ids=_chart_product_ids(account, chart_name), return_df=False)
    save_df_to_excel(
        df=load_portfolio_charts(account, chart_name=chart_name),
        file_name=charts_file_name(account, chart_name),
        folder_name=DATA_DIR,
    )


def load_portfolio_charts(
    account: Account, chart_name: str = PRODUCTS_CHART_FILE_NAME, chart_type: ChartType = ChartType.PRICE
) -> pd.DataFrame:
    """Saved charts of an account: date x product id (products with data only)."""
    chart_df = query_degiro_hist(_chart_product_ids(account, chart_name), columns=chart_type.value)
    chart_df.index.name, chart_df.columns.name = None, None
    return chart_df


def fetch_fx_charts(account: Account, degiro_conn: API | None = None):
    """Download the exchange rate charts into the database, with an Excel copy."""
    fetch_portfolio_charts(account, degiro_conn=degiro_conn, chart_name=FX_RATES_CHART_FILE_NAME)


# currencies through which a rate is computed when no pair links two currencies
CROSS_CURRENCIES = ('EUR', 'USD')


def exchange_rate(pair_prices: pd.DataFrame, currency: str, base: str) -> pd.Series | None:
    """Price of one unit of a currency in the base currency, from the prices of the currency pairs ({base}/{quote}
    columns): the direct pair, the inverse pair, or a cross rate through EUR or USD; None without any.
    """

    def from_pair(first: str, second: str) -> pd.Series | None:
        if f'{first}/{second}' in pair_prices:
            return pair_prices[f'{first}/{second}']
        if f'{second}/{first}' in pair_prices:
            return 1.0 / pair_prices[f'{second}/{first}']
        return None

    rate = from_pair(currency, base)
    for via in CROSS_CURRENCIES:
        if rate is not None:
            break
        if via in (currency, base):
            continue
        to_via, from_via = from_pair(currency, via), from_pair(via, base)
        if to_via is not None and from_via is not None:
            rate = to_via.mul(from_via)
    return None if rate is None else rate.rename(f'{currency}/{base}')


def load_fx_rates(
    account: Account,
    curr_foreign_lst: list[str],
    index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Exchange rates of the foreign currencies to the account currency ({currency}/{base}), with a column of ones for
    the base currency.
    """
    pairs = query_currency_pairs()
    pair_prices = load_portfolio_charts(account=account, chart_name=FX_RATES_CHART_FILE_NAME)
    pair_prices = pair_prices.rename(columns={product_id: pair for pair, product_id in pairs.items()})
    rates = []
    for cur in curr_foreign_lst:
        rate = exchange_rate(pair_prices, currency=cur, base=account.currency)
        if rate is None:
            logger.warning('Missing foreign exchange historical time series for %s/%s', cur, account.currency)
        else:
            rates.append(rate)
    fx_rates_df = pd.concat(rates, axis=1) if rates else pd.DataFrame(index=index)
    # dummy column of ones for domestic currency
    fx_rates_df[f'{account.currency}/{account.currency}'] = 1.0
    fx_rates_df.index = pd.to_datetime(fx_rates_df.index)
    return fx_rates_df
