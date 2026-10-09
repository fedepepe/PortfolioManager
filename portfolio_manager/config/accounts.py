"""The broker accounts managed by the dashboard."""

from enum import Enum
from typing import NamedTuple

from portfolio_manager.backtest.portfolio import Currencies
from portfolio_manager.config.account_settings import Benchmark, BenchmarkComponent


class Brokers(Enum):
    """Supported brokers."""

    DEGIRO = 'Degiro'
    IBS = 'IBS'


class Account(NamedTuple):
    """One account: name, broker, base currency, credentials file and the benchmark it starts with (the user can
    change it: the benchmark in use is saved in the database).
    """

    name: str
    broker: Brokers
    currency: Currencies
    config_file: str
    default_benchmark: Benchmark | None = None


class Accounts(Account, Enum):
    """The accounts."""

    DEGIRO_CHF = Account(
        name='Portfolio CHF',
        broker=Brokers.DEGIRO,
        currency=Currencies.CHF,
        config_file='config.json',
        default_benchmark=Benchmark(
            components=[
                BenchmarkComponent(label='IWDC', search='IE00B8BVCK12', weight=0.6),
                BenchmarkComponent(label='HYLD', search='HYLD.L', weight=0.2),
                BenchmarkComponent(label='STHC', search='STHC.SW', weight=0.2),
            ],
            rebalancing_freq='M',
        ),
    )
    DEGIRO_EUR = Account(
        name='Portfolio EUR',
        currency=Currencies.EUR,
        broker=Brokers.DEGIRO,
        config_file='config_2.json',
        default_benchmark=Benchmark(
            components=[
                BenchmarkComponent(label='SPYI', search='SPYI.DE', weight=0.6),
                BenchmarkComponent(label='HYLE', search='HYLE.DE', weight=0.2),
                BenchmarkComponent(label='IGLA', search='IE00BYZ28V50', weight=0.2),
            ],
            rebalancing_freq='M',
        ),
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
