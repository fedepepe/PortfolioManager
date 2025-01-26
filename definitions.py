import os
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
PRODUCTS_CHART_FILE_NAME = 'products'
FX_RATES_CHART_FILE_NAME = 'fx_rates'

# portfolio
BASE_CURRENCY = Currencies.CHF
PORTFOLIO_NAME = f'Portfolio {BASE_CURRENCY}'

# financial math constants
RISK_FREE_RATE = 0.0
