import os
from enum import Enum
from typing import NamedTuple, Optional, Dict, Tuple

from product_definitions import Currencies

# directories
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
DATA_ETF_DIR = os.path.join(ROOT_DIR, 'data/etf')
CONFIG_DIR = os.path.join(ROOT_DIR, 'config')
RESULTS_DIR = os.path.join(ROOT_DIR, 'results')
FIGURES_DIR = os.path.join(ROOT_DIR, 'figures')

for path in [DATA_DIR, CONFIG_DIR, RESULTS_DIR, FIGURES_DIR]:
	os.makedirs(path, exist_ok=True)

# data properties
DEFAULT_DATA_FREQ = 'B'

# product data
PRODUCTS_CHART_FILE_NAME = 'prod'
FX_RATES_CHART_FILE_NAME = 'fx_rates'

# portfolio
DEFAULT_PORTFOLIO_NAME = 'Portfolio'


class AccountDegiro(NamedTuple):
	name: str
	currency: Currencies
	config_file: str
	benchmark: Optional[Dict[str, Tuple[float, str, Optional[str]]]] = None


class Accounts(AccountDegiro, Enum):
	CHF = AccountDegiro(name='Portfolio CHF', currency=Currencies.CHF, config_file='config',
	                    benchmark={'IWDC': (0.6, 'M', 'IE00B8BVCK12'),
	                               'HYLD': (0.2, 'M', 'HYLD.L'),
	                               'STHC': (0.2, 'M', 'STHC.SW'), })
	EUR = AccountDegiro(name='Portfolio EUR', currency=Currencies.EUR, config_file='config_2',
	                    benchmark={'IWDC': (0.6, 'M', 'IE00B8BVCK12'),
	                               'IBC9': (0.2, 'M', 'IBC9.DE'),
	                               'IGLA': (0.2, 'M', 'IE00BYZ28V50'), })

	@classmethod
	def get_default_account(cls):
		return cls.CHF

	@classmethod
	def get_account_by_name(cls, name):
		found = [e for e in cls if e.name == name]
		return found[0] if found else None


# financial math constants
RISK_FREE_RATE = 0.0
