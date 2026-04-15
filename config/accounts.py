from enum import Enum
from typing import NamedTuple, Optional, Dict, Tuple

from engines.settings import State
from portfolio.portfolio import Currencies


class Brokers(Enum):
	DEGIRO = 'Degiro'
	IBS = 'IBS'


class Account(NamedTuple):
	name: str
	broker: Brokers
	currency: Currencies
	config_file: str
	state: State
	benchmark: Optional[Dict[str, Tuple[float, str, Optional[str]]]] = None


class Accounts(Account, Enum):
	DEGIRO_CHF = Account(name='Portfolio CHF',
	                     broker=Brokers.DEGIRO,
	                     currency=Currencies.CHF,
	                     config_file='config',
	                     state=State('Portfolio CHF'),
	                     benchmark={'IWDC': (0.6, 'M', 'IE00B8BVCK12'),
	                                'HYLD': (0.2, 'M', 'HYLD.L'),
	                                'STHC': (0.2, 'M', 'STHC.SW'), })
	DEGIRO_EUR = Account(name='Portfolio EUR',
	                     currency=Currencies.EUR,
	                     broker=Brokers.DEGIRO,
	                     config_file='config_2',
	                     state=State('Portfolio EUR'),
	                     benchmark={'SPYI': (0.6, 'M', 'SPYI.DE'),
	                                'HYLE': (0.2, 'M', 'HYLE.DE'),
	                                'IGLA': (0.2, 'M', 'IE00BYZ28V50'), })

	@classmethod
	def get_default_account(cls):
		return cls.DEGIRO_CHF

	@classmethod
	def get_account_by_name(cls, name):
		found = [e for e in cls if e.name == name]
		return found[0] if found else None
