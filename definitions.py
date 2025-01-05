import os
from product_definitions import Currencies

# directories
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
CONFIG_DIR = os.path.join(ROOT_DIR, 'config')
RESULTS_DIR = os.path.join(ROOT_DIR, 'results')
FIGURES_DIR = os.path.join(ROOT_DIR, 'figures')

# product data
PRODUCTS_CHART_FILE_NAME = 'products'
FX_RATES_CHART_FILE_NAME = 'fx_rates'

# portfolio
BASE_CURRENCY = Currencies.EUR
PORTFOLIO_NAME = f'Portfolio {BASE_CURRENCY}'

# financial math constants
RISK_FREE_RATE = 0.0
