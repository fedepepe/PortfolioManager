from os import listdir
from os.path import isfile, join
from typing import Optional

from degiro_connector.trading.api import API
from degiro_connector.trading.models.credentials import build_credentials

from definitions import CONFIG_DIR


def get_degiro_connection(file_name: Optional[str] = None) -> API:
	if file_name is None:
		# get file names in config folder
		file_name = [f for f in listdir(CONFIG_DIR) if isfile(join(CONFIG_DIR, f))][0]
	credentials = build_credentials(
		location=f"{CONFIG_DIR}/{file_name}.json",
	)
	conn = API(credentials=credentials)
	conn.connect()
	# DISPLAY SESSION_ID
	session_id = conn.connection_storage.session_id
	print(f"You are now connected, with the session id: {session_id}")
	return conn
