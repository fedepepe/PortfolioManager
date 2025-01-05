import pandas as pd

FREQ_DAYS_DICT = {'D': 1, 'B': 1, 'W': 7, '2W': 14, 'M': 30, '2M': 60}
ANN_FACTOR_DICT = {'H': 365 * 24, 'D': 365, 'B': 252, 'W': 52, '2W': 26, 'M': 12, '2M': 6, 'Q': 4, '2Q': 2, 'Y': 1}
FREQ_LABELS_DICT = {'hourly': 'H',
                    'daily': 'D',
                    'weekly': 'W',
                    'biweekly': '2W',
                    'monthly': 'M',
                    'bimonthly': '2M',
                    'quarterly': 'Q',
                    'biquarterly': '2Q',
                    'yearly': 'Y'}


def reset_time(ts: pd.Timestamp) -> pd.Timestamp:
	return ts.replace(hour=0, minute=0, second=0, microsecond=0)
