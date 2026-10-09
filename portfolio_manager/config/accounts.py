"""The broker accounts managed by the dashboard, added by the user and saved in the database."""

import re
from dataclasses import dataclass
from enum import Enum

from portfolio_manager.config.account_settings import DEFAULT_BENCHMARKS
from portfolio_manager.storage.queries import insert_account, query_accounts, query_benchmark, save_benchmark


class Brokers(Enum):
    """Supported brokers."""

    DEGIRO = 'Degiro'


@dataclass(frozen=True)
class Account:
    """A broker account: name (the portfolio name), broker, credentials file (in the credentials folder) and base
    currency (None until it is known).
    """

    name: str
    broker: Brokers
    credentials_file: str
    currency: str | None = None


def list_accounts() -> list[Account]:
    """The accounts, in the order they were added."""
    return [
        Account(
            name=row['name'],
            broker=Brokers(row['broker']),
            credentials_file=row['credentials_file'],
            currency=row['currency'],
        )
        for row in query_accounts()
    ]


def get_account(name: str | None) -> Account | None:
    """The account with the given name, None if there is none."""
    return next((account for account in list_accounts() if account.name == name), None)


def default_account() -> Account | None:
    """The account shown when none is selected: the first one added (None if there is no account)."""
    accounts = list_accounts()
    return accounts[0] if accounts else None


def credentials_file_name(account_name: str) -> str:
    """Credentials file proposed for a new account, e.g. portfolio_usd.json for "Portfolio USD"."""
    return re.sub(r'[^a-z0-9]+', '_', account_name.lower()).strip('_') + '.json'


def add_account(
    name: str, broker: Brokers, credentials_file: str | None = None, currency: str | None = None
) -> Account:
    """Add an account (ValueError if it cannot be added); with a known currency, it gets the default benchmark of the
    currency.
    """
    name = name.strip()
    if not name:
        raise ValueError('The account needs a name')
    credentials_file = credentials_file or credentials_file_name(name)
    if not re.fullmatch(r'[\w.-]+\.json', credentials_file):
        raise ValueError(f'Invalid credentials file name: {credentials_file} (a .json file name, without folders)')
    if currency is not None and not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError(f'Invalid currency: {currency} (three capital letters, e.g. EUR)')
    insert_account(name=name, broker=broker.value, credentials_file=credentials_file, currency=currency)
    account = Account(name=name, broker=broker, credentials_file=credentials_file, currency=currency)
    attach_default_benchmark(account)
    return account


def attach_default_benchmark(account: Account):
    """Save the default benchmark of the account currency if the account has no benchmark (saved settings are never
    changed).
    """
    if account.currency in DEFAULT_BENCHMARKS and query_benchmark(account.name) is None:
        save_benchmark(account.name, DEFAULT_BENCHMARKS[account.currency])
