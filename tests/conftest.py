# Shared fixtures: synthetic market data only (no Degiro, Yahoo Finance or saved portfolio data)
import numpy as np
import pandas as pd
import pytest


def make_prices(
    n_assets: int = 4, start: str = '2018-01-01', end: str = '2021-12-31', seed: int = 0, late_start: dict | None = None
) -> pd.DataFrame:
    # business-day prices following random walks; late_start maps a column to the date its history starts
    index = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    drifts = np.linspace(1e-4, 5e-4, n_assets)
    vols = np.linspace(0.005, 0.015, n_assets)
    returns = rng.normal(drifts, vols, size=(len(index), n_assets))
    prices = pd.DataFrame(
        100.0 * np.exp(np.cumsum(returns, axis=0)), index=index, columns=[f'A{n}' for n in range(n_assets)]
    )
    for column, first_date in (late_start or {}).items():
        prices.loc[prices.index < first_date, column] = np.nan
    return prices


@pytest.fixture
def prices() -> pd.DataFrame:
    return make_prices()


@pytest.fixture
def prices_late_asset() -> pd.DataFrame:
    # A3 starts trading in July 2021: little history at the end of 2021
    return make_prices(late_start={'A3': '2021-07-01'})
