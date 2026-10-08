"""The broker accounts managed by the dashboard."""

from enum import Enum
from typing import NamedTuple

from portfolio_manager.backtest.portfolio import Currencies


class Brokers(Enum):
    """Supported brokers."""

    DEGIRO = 'Degiro'
    IBS = 'IBS'


class Account(NamedTuple):
    """Settings of one account: name, broker, base currency, credentials file and benchmark."""

    name: str
    broker: Brokers
    currency: Currencies
    config_file: str
    benchmark: dict[str, tuple[float, str, str | None]] | None = None


class Accounts(Account, Enum):
    """The accounts; the benchmark maps each ticker to (weight, rebalancing frequency, ISIN or ticker)."""

    DEGIRO_CHF = Account(
        name='Portfolio CHF',
        broker=Brokers.DEGIRO,
        currency=Currencies.CHF,
        config_file='config.json',
        benchmark={
            'IWDC': (0.6, 'M', 'IE00B8BVCK12'),
            'HYLD': (0.2, 'M', 'HYLD.L'),
            'STHC': (0.2, 'M', 'STHC.SW'),
        },
    )
    DEGIRO_EUR = Account(
        name='Portfolio EUR',
        currency=Currencies.EUR,
        broker=Brokers.DEGIRO,
        config_file='config_2.json',
        benchmark={
            'SPYI': (0.6, 'M', 'SPYI.DE'),
            'HYLE': (0.2, 'M', 'HYLE.DE'),
            'IGLA': (0.2, 'M', 'IE00BYZ28V50'),
        },
    )

    @classmethod
    def get_default_account(cls):
        """Account used when none is selected."""
        return cls.DEGIRO_CHF

    @classmethod
    def get_account_by_name(cls, name):
        """Account with the given name, None if there is none."""
        found = [e for e in cls if e.name == name]
        return found[0] if found else None
