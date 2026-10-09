"""Login to the Degiro API, and the account information read at each connection."""

import dataclasses
import logging

from degiro_connector.trading.api import API
from degiro_connector.trading.models.credentials import build_credentials

from portfolio_manager.config.accounts import Account, attach_default_benchmark, default_account
from portfolio_manager.config.settings import CREDENTIALS_DIR
from portfolio_manager.storage.queries import update_account_currency, upsert_currency_pairs

logger = logging.getLogger(__name__)


def get_degiro_connection(account: Account | None = None) -> API:
    """Logs in to the account with its credentials file; without an account (market data, the same for every account)
    the default account is used. The account number, needed by some requests, is read from the client details when the
    credentials file does not have it.
    """
    if account is None:
        account = default_account()
    credentials = build_credentials(
        location=f'{CREDENTIALS_DIR}/{account.credentials_file}',
    )
    conn = API(credentials=credentials)
    conn.connect()
    # the session id is a login token, the account number a personal datum: never print or log them
    if conn.credentials.int_account is None:
        conn.credentials.int_account = conn.get_client_details()['data']['intAccount']
    logger.info('Connected to Degiro as %s', account.name)
    return conn


def account_info(conn: API) -> tuple[str, dict[str, int]]:
    """Base currency of the account and the currency pairs traded at Degiro ({base}/{quote} -> product id of the
    exchange rate; pairs without a product are left out).
    """
    info = conn.get_account_info()
    if info is None:
        raise ConnectionError('Degiro returned no account information')
    data = info['data']
    pairs = {
        f'{name[:3]}/{name[3:]}': int(pair['id'])
        for name, pair in data.get('currencyPairs', {}).items()
        if len(name) == 6 and int(pair.get('id', -1)) > 0
    }
    return data['baseCurrency'], pairs


def connect_account(account: Account, conn: API | None = None) -> Account:
    """Log in, save the base currency of the account (ValueError if it differs from the saved one) and the currency
    pairs, and give the account the default benchmark of its currency if it has no benchmark. Returns the account with
    its currency.
    """
    if conn is None:
        conn = get_degiro_connection(account=account)
    currency, pairs = account_info(conn)
    if account.currency is None:
        update_account_currency(account.name, currency)
        account = dataclasses.replace(account, currency=currency)
    elif account.currency != currency:
        raise ValueError(f'{account.name} is saved with currency {account.currency}, but Degiro reports {currency}')
    upsert_currency_pairs(pairs)
    attach_default_benchmark(account)
    return account
