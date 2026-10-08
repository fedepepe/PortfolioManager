from typing import Optional

from degiro_connector.trading.api import API
from degiro_connector.trading.models.credentials import build_credentials

from config.accounts import Accounts
from config.definitions import CREDENTIALS_DIR


def get_degiro_connection(account: Optional[Accounts] = None) -> API:
	# logs in to the account with its credentials file; without an account (market data, the same for every
	# account) the default account is used
	if account is None:
		account = Accounts.get_default_account()
	credentials = build_credentials(
		location=f"{CREDENTIALS_DIR}/{account.config_file}",
	)
	conn = API(credentials=credentials)
	conn.connect()
	# DISPLAY SESSION_ID
	session_id = conn.connection_storage.session_id
	print(f"You are now connected, with the session id: {session_id}")
	return conn
