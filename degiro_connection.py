from degiro_connector.trading.api import API
from degiro_connector.trading.models.credentials import build_credentials

from definitions import CONFIG_DIR


credentials = build_credentials(
	location=f"{CONFIG_DIR}/config.json",
)
TRADING_API = API(credentials=credentials)
TRADING_API.connect()
# ACCESS SESSION_ID
session_id = TRADING_API.connection_storage.session_id
print(f"You are now connected, with the session id: {session_id}")
