"""Paths of the data folders and project-wide constants."""

import os

# directories (created when a file is first written to them)
# portfolio_manager/config/settings.py -> project root (three levels up)
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_DIR, 'data')
CREDENTIALS_DIR = os.path.join(PROJECT_DIR, 'credentials')
RESULTS_DIR = os.path.join(PROJECT_DIR, 'results')

# data properties
DEFAULT_DATA_FREQ = 'B'
DEFAULT_CORR_DATA_FREQ = 'W-WED'

# product data
PRODUCTS_CHART_FILE_NAME = 'prod'
FX_RATES_CHART_FILE_NAME = 'fx_rates'

# financial math constants
RISK_FREE_RATE = 0.0
