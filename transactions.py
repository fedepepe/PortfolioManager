import logging
from datetime import date

import pandas as pd
from degiro_connector.trading.models.transaction import HistoryRequest

import file_utils as fu
from connection import get_connection

logging.basicConfig(level=logging.DEBUG)


def fetch_tx_history() -> pd.DataFrame:
	# FETCH ACCOUNT OVERVIEW
	transactions_history = get_connection().get_transactions_history(
		transaction_request=HistoryRequest(
			from_date=date(year=date.today().year - 10, month=1, day=1),
			to_date=date.today(),
		),
		raw=False,
	)

	tx_history_df = pd.DataFrame()
	for tx in transactions_history.data:
		columns, values = zip(*tx)
		ser = pd.Series(values, columns)
		tx_history_df = pd.concat([tx_history_df, ser.to_frame().T])
	tx_history_df = tx_history_df.set_index('date')
	tx_history_df = tx_history_df.sort_index()
	tx_history_df.index = tx_history_df.index.tz_convert(None)
	fu.save_df_to_excel(df=tx_history_df, file_name='tx_history', folder='data')
	return tx_history_df


def load_tx_history() -> pd.DataFrame:
	tx_history_df = fu.load_df_from_excel(file_name='tx_history', folder='data')
	return tx_history_df


if __name__ == '__main__':
	fetch_tx_history()
