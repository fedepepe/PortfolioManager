from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.credentials import build_credentials


def get_connection():
	credentials = build_credentials(
		location="config/config.json",
	)
	trading_api = TradingAPI(credentials=credentials)
	trading_api.connect()
	# ACCESS SESSION_ID
	session_id = trading_api.connection_storage.session_id
	print(f"You are now connected, with the session id: {session_id}")
	return trading_api
