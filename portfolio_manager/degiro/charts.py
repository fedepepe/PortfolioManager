"""Degiro price charts of the portfolio products and of the exchange rates."""

import logging
from enum import Enum

import pandas as pd
from degiro_connector.quotecast.models.chart import ChartRequest, Interval
from degiro_connector.quotecast.tools.chart_fetcher import ChartFetcher, SeriesFormatter
from degiro_connector.trading.api import API

from portfolio_manager.config.accounts import Accounts
from portfolio_manager.config.settings import DATA_DIR, FX_RATES_CHART_FILE_NAME, PRODUCTS_CHART_FILE_NAME
from portfolio_manager.degiro.connection import get_degiro_connection
from portfolio_manager.degiro.definitions import ProductTypes
from portfolio_manager.degiro.products import fetch_product_info
from portfolio_manager.storage.files import save_df_to_excel
from portfolio_manager.storage.queries import (
    insert_degiro_hist,
    query_account_product_ids,
    query_degiro_hist,
    query_products,
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


def charts_file_name(account: Accounts, chart_name: str, chart_type: ChartType = ChartType.PRICE) -> str:
    """Excel copy of the saved charts of an account."""
    return f'{account.name}_{chart_name}_{chart_type.value}'


def _chart_product_ids(account: Accounts, chart_name: str) -> list[int]:
    # products of the account, or every currency (exchange rates)
    if chart_name == FX_RATES_CHART_FILE_NAME:
        return query_products(product_type=ProductTypes.CURRENCY)['id'].to_list()
    return query_account_product_ids(account.name)


def fetch_portfolio_charts(
    account: Accounts, degiro_conn: API | None = None, chart_name: str = PRODUCTS_CHART_FILE_NAME
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
    account: Accounts, chart_name: str = PRODUCTS_CHART_FILE_NAME, chart_type: ChartType = ChartType.PRICE
) -> pd.DataFrame:
    """Saved charts of an account: date x product id (products with data only)."""
    chart_df = query_degiro_hist(_chart_product_ids(account, chart_name), columns=chart_type.value)
    chart_df.index.name, chart_df.columns.name = None, None
    return chart_df


def fetch_fx_charts(account: Accounts, degiro_conn: API | None = None):
    """Download the exchange rate charts into the database, with an Excel copy."""
    fetch_portfolio_charts(account, degiro_conn=degiro_conn, chart_name=FX_RATES_CHART_FILE_NAME)


def load_fx_rates(
    account: Accounts,
    curr_foreign_lst: list[str],
    index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Exchange rates of the foreign currencies to the account currency ({currency}/{base}), with a column of ones for
    the base currency.
    """
    results_df = query_products(product_type=ProductTypes.CURRENCY)
    fx_rates_df_tmp = load_portfolio_charts(account=account, chart_name=FX_RATES_CHART_FILE_NAME)
    fx_rates_df_tmp = fx_rates_df_tmp.rename(columns=dict(results_df.loc[fx_rates_df_tmp.columns, 'name']))
    fx_rates_df_tmp.columns = [c.split(' X-RATE')[0].replace('-', '/') for c in fx_rates_df_tmp.columns]
    # additional fx
    if 'CHF/GBP' not in fx_rates_df_tmp and 'GBP/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'GBP/CHF'] = fx_rates_df_tmp['EUR/CHF'].div(fx_rates_df_tmp['EUR/GBP'])
    if 'CHF/JPY' not in fx_rates_df_tmp and 'JPY/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'JPY/CHF'] = fx_rates_df_tmp['EUR/CHF'].div(fx_rates_df_tmp['EUR/JPY'])
    if 'CHF/CAD' not in fx_rates_df_tmp and 'CAD/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'CAD/CHF'] = fx_rates_df_tmp['EUR/CHF'].div(fx_rates_df_tmp['EUR/CAD'])
    if 'CHF/AUD' not in fx_rates_df_tmp and 'AUD/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'AUD/CHF'] = fx_rates_df_tmp['EUR/CHF'].div(fx_rates_df_tmp['EUR/AUD'])
    if 'CHF/DKK' not in fx_rates_df_tmp and 'DKK/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'DKK/CHF'] = fx_rates_df_tmp['USD/CHF'].div(fx_rates_df_tmp['USD/DKK'])
    if 'CHF/SEK' not in fx_rates_df_tmp and 'SEK/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'SEK/CHF'] = fx_rates_df_tmp['USD/CHF'].div(fx_rates_df_tmp['USD/SEK'])
    if 'CHF/NOK' not in fx_rates_df_tmp and 'NOK/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'NOK/CHF'] = fx_rates_df_tmp['USD/CHF'].div(fx_rates_df_tmp['USD/NOK'])
    if 'CHF/PLN' not in fx_rates_df_tmp and 'PLN/CHF' not in fx_rates_df_tmp:
        fx_rates_df_tmp.loc[:, 'PLN/CHF'] = fx_rates_df_tmp['USD/CHF'].div(fx_rates_df_tmp['USD/PLN'])
    fx_rates_df = pd.DataFrame()
    for cur in curr_foreign_lst:
        if f'{cur}/{account.currency}' in fx_rates_df_tmp:
            fx_rates_df = pd.concat([fx_rates_df, fx_rates_df_tmp[f'{cur}/{account.currency}']], axis=1)
        elif f'{account.currency}/{cur}' in fx_rates_df_tmp:
            ser = (1.0 / fx_rates_df_tmp[f'{account.currency}/{cur}']).rename(f'{cur}/{account.currency}')
            fx_rates_df = pd.concat([fx_rates_df, ser], axis=1)
        else:
            logger.warning('Missing foreign exchange historical time series for %s/%s', cur, account.currency)
    if fx_rates_df.empty:
        fx_rates_df = fx_rates_df.reindex(index=index)
    # dummy column of ones for domestic currency
    fx_rates_df[f'{account.currency}/{account.currency}'] = 1.0
    fx_rates_df.index = pd.to_datetime(fx_rates_df.index)
    return fx_rates_df
