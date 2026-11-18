"""Date helpers and annualization factors of the sampling frequencies."""

import pandas as pd

ANN_FACTOR_DICT = {'H': 365 * 24, 'D': 365, 'B': 261, 'W': 52, '2W': 26, 'M': 12, '2M': 6, 'Q': 4, '2Q': 2, 'Y': 1}


def reset_time(ts: pd.Timestamp) -> pd.Timestamp:
    """The date without its time."""
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)
