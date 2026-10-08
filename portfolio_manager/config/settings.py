"""Paths of the data folders and project-wide constants."""

import os

# directories (created when a file is first written to them)
# portfolio_manager/config/settings.py -> project root (three levels up)
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_DIR, 'data')
DATA_ETF_DIR = os.path.join(DATA_DIR, 'etf')
CREDENTIALS_DIR = os.path.join(PROJECT_DIR, 'credentials')
STATE_DIR = os.path.join(PROJECT_DIR, 'state')
RESULTS_DIR = os.path.join(PROJECT_DIR, 'results')
FIGURES_DIR = os.path.join(PROJECT_DIR, 'figures')

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
