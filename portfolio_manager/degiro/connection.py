"""Login to the Degiro API."""

import logging

from degiro_connector.trading.api import API
from degiro_connector.trading.models.credentials import build_credentials

from portfolio_manager.config.accounts import Accounts
from portfolio_manager.config.settings import CREDENTIALS_DIR

logger = logging.getLogger(__name__)


def get_degiro_connection(account: Accounts | None = None) -> API:
    """Logs in to the account with its credentials file; without an account (market data, the same for every account)
    the default account is used.
    """
    if account is None:
        account = Accounts.get_default_account()
    credentials = build_credentials(
        location=f'{CREDENTIALS_DIR}/{account.config_file}',
    )
    conn = API(credentials=credentials)
    conn.connect()
    # the session id is a login token: never print or log it
    logger.info('Connected to Degiro as %s', account.name)
    return conn
