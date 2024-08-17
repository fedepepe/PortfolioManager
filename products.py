import logging
from enum import Enum
from typing import List

import pandas as pd
from degiro_connector.quotecast.models.chart import ChartRequest, Interval
from degiro_connector.quotecast.tools.chart_fetcher import ChartFetcher, SeriesFormatter

from connection import get_connection
import file_utils as fu

logging.basicConfig(level=logging.DEBUG)


class ChartType(str, Enum):
	PRICE = 'price'
	OHLC = 'ohlc'
	VOLUME = 'volume'


def get_product_info(product_ids: int | List[int] = 11853206):
	if isinstance(product_ids, int):
		product_ids = [product_ids]

	# FETCH PRODUCT INFO
	product_info = get_connection().get_products_info(
		product_list=product_ids,
		raw=False,
	)
	return product_info


def fetch_chart(product_ids: int | List[int] = 11853206,
                chart_type: ChartType = ChartType.PRICE
                ) -> pd.DataFrame:
	if isinstance(product_ids, int):
		product_ids = [product_ids]

	product_info = get_product_info(product_ids)
	vwd_ids = [prod.vwd_id for prod in product_info.data.values()]

	# FETCH DATA
	client_details_table = get_connection().get_client_details()

	# EXTRACT SOME DATA
	int_account = client_details_table['data']['intAccount']
	user_token = client_details_table['data']['id']

	chart_fetcher = ChartFetcher(user_token=user_token)
	chart_df = pd.DataFrame()
	for vwd_id in vwd_ids:
		chart_request = ChartRequest(
			culture="en-US",
			period=Interval.P10Y,
			requestid="1",
			resolution=Interval.P1D,
			series=[f"{chart_type.value}:issueid:{vwd_id}"],
			tz="Europe/Paris",
		)
		chart = chart_fetcher.get_chart(
			chart_request=chart_request,
			raw=False,
		)

		if chart is None:
			continue

		# for chart, ser in zip(charts, ):
		df = SeriesFormatter.format(series=chart.series[0]).to_pandas().rename(columns={'price': vwd_id})
		df = df.set_index('timestamp')
		chart_df = pd.concat([chart_df, df], axis=1)
	chart_df = chart_df.groupby(by=chart_df.columns, axis=1).mean()
	return chart_df


def save_charts(chart_df: pd.DataFrame,
                chart_type: ChartType = ChartType.PRICE):
	try:
		df_old = fu.load_df_from_excel(file_name=f'products_{chart_type.value}', folder='data')
	except FileNotFoundError:
		df_old = pd.DataFrame()
	chart_df = pd.concat([df_old, chart_df], axis=1)
	chart_df = chart_df.groupby(by=chart_df.columns, axis=1).mean()
	fu.save_df_to_excel(df=chart_df, file_name=f'products_{chart_type.value}', folder='data')


if __name__ == '__main__':
	fetch_chart()
