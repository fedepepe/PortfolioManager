from os import listdir
from os.path import isfile, join
from typing import Optional

from degiro_connector.trading.api import API
from degiro_connector.trading.models.credentials import build_credentials

from config.definitions import CREDENTIALS_DIR


def get_degiro_connection(file_name: Optional[str] = None) -> API:
	if file_name is None:
		# get file names in credentials folder
		file_name = [f for f in listdir(CREDENTIALS_DIR) if isfile(join(CREDENTIALS_DIR, f))][0]
	credentials = build_credentials(
		location=f"{CREDENTIALS_DIR}/{file_name}.json",
	)
	conn = API(credentials=credentials)
	conn.connect()
	# DISPLAY SESSION_ID
	session_id = conn.connection_storage.session_id
	print(f"You are now connected, with the session id: {session_id}")
	return conn
