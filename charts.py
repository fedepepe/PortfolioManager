import warnings
from enum import Enum
from typing import List, Optional

import pandas as pd
from degiro_connector.quotecast.models.chart import ChartRequest, Interval
from degiro_connector.quotecast.tools.chart_fetcher import ChartFetcher, SeriesFormatter
from degiro_connector.trading.api import API

import file_utils as fu
from definitions import DATA_DIR, PRODUCTS_CHART_FILE_NAME, FX_RATES_CHART_FILE_NAME
from definitions import DEFAULT_PORTFOLIO_NAME
from degiro_connection import get_degiro_connection
from product_definitions import Currencies
from products import fetch_product_info, load_portfolio_products, query_products, ProductTypes
from sql import insert_close


class ChartType(str, Enum):
	PRICE = 'price'
	OHLC = 'ohlc'
	VOLUME = 'volume'


def fetch_charts(degiro_conn: Optional[API] = None,
                 product_ids: Optional[int | List[int]] = None,
                 product_info_df: Optional[pd.DataFrame] = None,
                 chart_type: ChartType = ChartType.PRICE,
                 period: Optional[Interval] = Interval.P10Y,
                 resolution: Optional[Interval] = Interval.P1D,
                 rename_columns_to: str = 'symbols',
                 ) -> pd.DataFrame:
	if degiro_conn is None:
		degiro_conn = get_degiro_connection()
	if product_info_df is None:
		if isinstance(product_ids, int):
			product_ids = [product_ids]
		# GET PRODUCT INFO
		product_info_df = fetch_product_info(degiro_conn=degiro_conn, product_ids=product_ids)
	else:
		product_ids = product_info_df['id'].astype(int).to_list()
	if len(product_ids) > 100:
		rename_columns_to = 'ids'
	# ESTABLISH CONNECTION
	client_details_table = degiro_conn.get_client_details()
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
	# restrict product_info_df to entries with a valid vwd_id
	product_info_df = product_info_df[product_info_df['vwd_id'].notna()]
	product_ids = product_info_df['id'].astype(int).to_list()
	vwd_ids = [product_info_df.loc[prod_id, 'vwd_id'] for prod_id in product_ids]
	symbols = [product_info_df.loc[prod_id, 'symbol'] for prod_id in product_ids]
	for vwd_id, product_id, symbol in zip(vwd_ids, product_ids, symbols):
		chart_request = ChartRequest(
			culture="en-US",
			period=period,
			requestid="1",
			resolution=resolution,
			series=[f"{chart_type.value}:issueid:{int(vwd_id)}"],
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
                chart_type: ChartType = ChartType.PRICE,
                portfolio_name: str = DEFAULT_PORTFOLIO_NAME):
	try:
		df_old = fu.load_df_from_excel(file_name=f'{chart_name}_{chart_type.value}', folder=DATA_DIR)
	except FileNotFoundError:
		df_old = pd.DataFrame()
	chart_df = pd.concat([df_old, chart_df], axis=1)
	chart_df = chart_df.T.groupby(by=chart_df.columns).mean().T
	fu.save_df_to_excel(df=chart_df, file_name=f'{portfolio_name}_{chart_name}_{chart_type.value}', folder=DATA_DIR)


def fetch_portfolio_charts(degiro_conn: Optional[API] = None,
                           portfolio_name: str = DEFAULT_PORTFOLIO_NAME):
	products_df = load_portfolio_products(portfolio_name=portfolio_name)
	chart_df = fetch_charts(degiro_conn=degiro_conn, product_ids=list(set(products_df['id'].astype(int).to_list())))
	save_charts(chart_df=chart_df, portfolio_name=portfolio_name)


def load_portfolio_charts(chart_name: str = PRODUCTS_CHART_FILE_NAME,
                          chart_type: ChartType = ChartType.PRICE,
                          portfolio_name: str = DEFAULT_PORTFOLIO_NAME) -> pd.DataFrame:
	chart_df = fu.load_df_from_excel(file_name=f'{portfolio_name}_{chart_name}_{chart_type.value}', folder=DATA_DIR)
	return chart_df


def fetch_fx_charts(degiro_conn: Optional[API] = None,
                    portfolio_name: str = DEFAULT_PORTFOLIO_NAME):
	results_df = query_products(product_type=ProductTypes.CURRENCY)
	chart_df = fetch_charts(degiro_conn=degiro_conn, product_ids=results_df['id'].to_list())
	save_charts(chart_df=chart_df, chart_name=FX_RATES_CHART_FILE_NAME, portfolio_name=portfolio_name)


def load_fx_rates(curr_foreign_lst: List[str],
                  curr_base: Currencies,
                  index: pd.DatetimeIndex,
                  portfolio_name: str = DEFAULT_PORTFOLIO_NAME
                  ) -> pd.DataFrame:
	fx_rates_df_tmp = load_portfolio_charts(chart_name=FX_RATES_CHART_FILE_NAME, portfolio_name=portfolio_name)
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
		if f'{cur}/{curr_base}' in fx_rates_df_tmp:
			fx_rates_df = pd.concat([fx_rates_df, fx_rates_df_tmp[f'{cur}/{curr_base}']], axis=1)
		elif f'{curr_base}/{cur}' in fx_rates_df_tmp:
			ser = (1. / fx_rates_df_tmp[f'{curr_base}/{cur}']).rename(f'{cur}/{curr_base}')
			fx_rates_df = pd.concat([fx_rates_df, ser], axis=1)
		else:
			warnings.warn(f'Warning! Missing foreign exchange historical time series for '
			              f'{cur}/{curr_base}')
	if fx_rates_df.empty:
		fx_rates_df = fx_rates_df.reindex(index=index)
	# dummy column of ones for domestic currency
	fx_rates_df[f'{curr_base}/{curr_base}'] = 1.0
	fx_rates_df.index = pd.to_datetime(fx_rates_df.index)
	return fx_rates_df


if __name__ == '__main__':
	fetch_fx_charts()
