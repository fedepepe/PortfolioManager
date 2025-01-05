from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.credentials import build_credentials

from definitions import CONFIG_DIR


credentials = build_credentials(
	location=f"{CONFIG_DIR}/config_2.json",
)
TRADING_API = TradingAPI(credentials=credentials)
TRADING_API.connect()
# ACCESS SESSION_ID
session_id = TRADING_API.connection_storage.session_id
print(f"You are now connected, with the session id: {session_id}")
