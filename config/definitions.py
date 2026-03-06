import os

# directories
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, '../data')
DATA_ETF_DIR = os.path.join(ROOT_DIR, '../data/etf')
CREDENTIALS_DIR = os.path.join(ROOT_DIR, '../credentials')
STATE_DIR = os.path.join(ROOT_DIR, '../state')
RESULTS_DIR = os.path.join(ROOT_DIR, '../results')
FIGURES_DIR = os.path.join(ROOT_DIR, '../figures')

for path in [DATA_DIR, CREDENTIALS_DIR, RESULTS_DIR, FIGURES_DIR]:
	os.makedirs(path, exist_ok=True)

# data properties
DEFAULT_DATA_FREQ = 'B'
DEFAULT_CORR_DATA_FREQ = 'W-WED'

# product data
PRODUCTS_CHART_FILE_NAME = 'prod'
FX_RATES_CHART_FILE_NAME = 'fx_rates'

# portfolio
DEFAULT_PORTFOLIO_NAME = 'Portfolio'

# financial math constants
RISK_FREE_RATE = 0.0
