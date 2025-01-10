from enum import Enum
from typing import List, Optional

import pandas as pd
from degiro_connector.quotecast.models.chart import ChartRequest, Interval
from degiro_connector.quotecast.tools.chart_fetcher import ChartFetcher, SeriesFormatter

import file_utils as fu
from definitions import DATA_DIR, PRODUCTS_CHART_FILE_NAME, FX_RATES_CHART_FILE_NAME
from degiro_connection import TRADING_API
from products import fetch_product_info, load_portfolio_products, query_products
from sql import insert_close


class ChartType(str, Enum):
    PRICE = 'price'
    OHLC = 'ohlc'
    VOLUME = 'volume'


def fetch_charts(product_ids: Optional[int | List[int]] = None,
                 product_info_df: Optional[pd.DataFrame] = None,
                 chart_type: ChartType = ChartType.PRICE,
                 period: Optional[Interval] = Interval.P10Y,
                 resolution: Optional[Interval] = Interval.P1D,
                 rename_columns_to: str = 'symbols',
                 ) -> pd.DataFrame:
    if product_info_df is None:
        if isinstance(product_ids, int):
            product_ids = [product_ids]
        # GET PRODUCT INFO
        product_info_df = fetch_product_info(product_ids)
    else:
        product_ids = product_info_df['id'].to_list()
    if len(product_ids) > 100:
        rename_columns_to = 'ids'
    # ESTABLISH CONNECTION
    client_details_table = TRADING_API.get_client_details()
    int_account = client_details_table['data']['intAccount']
    user_token = client_details_table['data']['id']
    # FETCH DATA
    chart_fetcher = ChartFetcher(user_token=user_token)
    chart_df = pd.DataFrame()
    if not all([s == 'issueid' for s in product_info_df['vwd_identifier_type'].to_list()]):
        for prod_id in product_info_df.index:
            if product_info_df.loc[prod_id, 'vwd_identifier_type'] != 'issueid':
                vwd_id_found = (product_info_df.loc[
                    (product_info_df['name'] == product_info_df.loc[prod_id, 'name']) &
                    (product_info_df['vwd_identifier_type'] == 'issueid'),
                    'vwd_id'])
                if not vwd_id_found.empty:
                    product_info_df.loc[prod_id, 'vwd_id'] = int(vwd_id_found.iloc[0])
    vwd_ids = [product_info_df.loc[prod_id, 'vwd_id'] for prod_id in product_ids]
    symbols = [product_info_df.loc[prod_id, 'symbol'] for prod_id in product_ids]
    for vwd_id, product_id, symbol in zip(vwd_ids, product_ids, symbols):
        chart_request = ChartRequest(
            culture="en-US",
            period=period,
            requestid="1",
            resolution=resolution,
            series=[f"{chart_type.value}:issueid:{vwd_id}"],
            tz="Europe/Paris",
        )
        data = chart_fetcher.get_chart(
            chart_request=chart_request,
            raw=False,
        )
        if data is None:
            continue
        df = SeriesFormatter.format(series=data.series[0]).to_pandas()
        if rename_columns_to == 'ids':
            df = df.rename(columns={chart_type.value: product_id})
        elif rename_columns_to == 'symbols':
            if symbol is not None:
                df = df.rename(columns={chart_type.value: symbol})
            else:
                df = df.rename(columns={chart_type.value: product_id})
        else:
            raise NotImplementedError
        df = df.set_index('timestamp')
        if len(product_ids) < 100:  # use pandas
            chart_df = pd.concat([chart_df, df], axis=1)
        else:  # dump data directly to database
            insert_close(series=df[product_id])
    chart_df = chart_df.T.groupby(by=chart_df.columns).mean().T
    return chart_df


def save_charts(chart_df: pd.DataFrame,
                chart_name: str = PRODUCTS_CHART_FILE_NAME,
                chart_type: ChartType = ChartType.PRICE):
    try:
        df_old = fu.load_df_from_excel(file_name=f'{chart_name}_{chart_type.value}', folder=DATA_DIR)
    except FileNotFoundError:
        df_old = pd.DataFrame()
    chart_df = pd.concat([df_old, chart_df], axis=1)
    chart_df = chart_df.T.groupby(by=chart_df.columns).mean().T
    fu.save_df_to_excel(df=chart_df, file_name=f'{chart_name}_{chart_type.value}', folder=DATA_DIR)


def fetch_portfolio_charts():
    products_df = load_portfolio_products()
    chart_df = fetch_charts(product_ids=list(set(products_df['id'].astype(int).to_list())))
    save_charts(chart_df=chart_df)


def load_portfolio_charts(chart_name: str = PRODUCTS_CHART_FILE_NAME,
                          chart_type: ChartType = ChartType.PRICE) -> pd.DataFrame:
    chart_df = fu.load_df_from_excel(file_name=f'{chart_name}_{chart_type.value}', folder=DATA_DIR)
    return chart_df


def fetch_fx_charts():
    results_df = query_products(product_type='CURRENCY')
    chart_df = fetch_charts(product_ids=results_df['id'].to_list())
    save_charts(chart_df=chart_df, chart_name=FX_RATES_CHART_FILE_NAME)


if __name__ == '__main__':
    fetch_fx_charts()
