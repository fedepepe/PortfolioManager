"""Returns of the instruments of the Tactical Asset Allocation research, each in its own currency, and the cash rate of
each currency.

Total returns include the distributions (adjusted closes). A currency pair BASE/QUOTE is held as a deposit in the base
currency, valued in the quote currency: its total return is the change of the exchange rate plus the interest of the
base currency, the return of a forward contract once the interest of the quote currency is subtracted. Excess returns
subtract the cash rate of the currency of the instrument (for a pair, the quote currency): they are the returns of the
instrument hedged into any currency, the same for every investor, and what the signals are tested on.

Cash rates: the overnight or 3-month rate of each currency, a central bank policy rate where none is stored (config.taa
rate series); interest accrues day by day on calendar days (actual/360, actual/365 for the currencies quoted so).
Before the first rate of a currency, its excess returns are unknown.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from portfolio_manager.market_data.history import listing_currencies, load_adjusted_closes, major_currency
from portfolio_manager.storage.queries import query_interest_rates, query_taa_instruments

# series giving the cash rate of each currency, in order of preference (each day, the first one with a value): EONIA
# before the euro short-term rate
CASH_RATE_SERIES = {
    'USD': ['USD_TBILL_3M'],
    'EUR': ['EUR_ESTR', 'EUR_EONIA'],
    'CHF': ['CHF_SARON'],
    'GBP': ['GBP_SONIA'],
    'JPY': ['JPY_POLICY'],
    'AUD': ['AUD_POLICY'],
    'CAD': ['CAD_POLICY'],
    'NZD': ['NZD_POLICY'],
    'SEK': ['SEK_POLICY'],
    'NOK': ['NOK_POLICY'],
}
DAYS_PER_YEAR = {'GBP': 365, 'JPY': 365, 'AUD': 365, 'CAD': 365, 'NZD': 365}  # money market convention, 360 otherwise
CURRENCY_GROUP = 'Currencies'


def is_currency_pair(ticker: str) -> bool:
    """True for a Yahoo Finance currency pair (e.g. EURUSD=X)."""
    return ticker.endswith('=X') and len(ticker) == 8


def business_days(prices: pd.DataFrame) -> pd.DataFrame:
    """Prices on every business day from the first to the last date, each carried over the days its market was closed
    (a holiday: no change), only within its own history.
    """
    days = pd.bdate_range(prices.index.min(), prices.index.max())
    filled = prices.reindex(prices.index.union(days)).ffill().reindex(days)
    alive = prices.notna().reindex(days).fillna(False)
    started, ended = alive.cummax(), alive[::-1].cummax()[::-1]
    return filled.where(started & ended)


def deposit_growth(rates: pd.Series, days_per_year: int) -> pd.Series:
    """Value of a deposit (1 on the first date with a rate): each day it earns the rate of the previous date over the
    calendar days in between; unknown before the first rate.
    """
    days = rates.index.to_series().diff().dt.days
    factors = (1.0 + rates.shift(1) * days / days_per_year).fillna(1.0)
    return factors.cumprod().where(rates.notna())


@dataclass
class ResearchData:
    """Prices of the instruments and cash rates of the currencies, on business days.

    prices: total return index of each instrument in its own currency (date x ticker); a pair includes the interest
    of its base currency. instruments: by ticker, its group, label and currency (the quote currency of a pair).
    cash_rates: date x currency, decimals per year.
    """

    prices: pd.DataFrame
    instruments: pd.DataFrame
    cash_rates: pd.DataFrame

    def period_prices(self, freq: str = 'B') -> pd.DataFrame:
        """Prices at the end of each period (B: every business day, W-FRI, M, ...)."""
        return self.prices if freq == 'B' else self.prices.resample(freq).last()

    def returns(self, freq: str = 'B') -> pd.DataFrame:
        """Total returns of the instruments over each period, in their own currency."""
        return self.period_prices(freq).pct_change(fill_method=None)

    def cash_returns(self, freq: str = 'B') -> pd.DataFrame:
        """Return of a deposit in each currency over each period (date x currency)."""
        growth = pd.DataFrame(
            {c: deposit_growth(self.cash_rates[c], DAYS_PER_YEAR.get(c, 360)) for c in self.cash_rates}
        )
        growth = growth if freq == 'B' else growth.resample(freq).last()
        return growth.pct_change(fill_method=None)

    def excess_returns(self, freq: str = 'B') -> pd.DataFrame:
        """Total returns minus the cash return of the currency of each instrument (unknown without a cash rate)."""
        returns = self.returns(freq)
        cash = self.cash_returns(freq)
        currency_cash = pd.DataFrame(
            {t: cash[c] if c in cash else np.nan for t, c in self.instruments['currency'].items()}, index=cash.index
        )
        return returns - currency_cash.reindex(index=returns.index, columns=returns.columns)


def cash_rates(currencies: list[str], days: pd.DatetimeIndex) -> pd.DataFrame:
    """Cash rate of each currency on each day (decimals per year): the first series of CASH_RATE_SERIES with a value,
    carried over the days without publication; unknown before the first value.
    """
    keys = sorted({key for c in currencies for key in CASH_RATE_SERIES.get(c, [])})
    stored = query_interest_rates(keys)
    stored = stored.reindex(stored.index.union(days)).ffill().reindex(days)
    rates = {}
    for currency in currencies:
        series = pd.Series(np.nan, index=days)
        for key in CASH_RATE_SERIES.get(currency, []):
            if key in stored:
                series = series.fillna(stored[key])
        rates[currency] = series
    return pd.DataFrame(rates, index=days)


def load_research_data(tickers: list[str] | None = None) -> ResearchData:
    """Prices and cash rates of the instruments of the research (those in use, if no tickers are given), from their
    stored history.
    """
    instruments = pd.DataFrame(query_taa_instruments()).set_index('ticker')[['group', 'label']]
    if tickers is not None:
        instruments = instruments.reindex(tickers)
    currencies = {t: major_currency(c)[0] for t, c in listing_currencies(instruments.index.to_list()).items()}
    instruments['currency'] = pd.Series(currencies)
    for ticker in instruments.index[instruments.index.map(is_currency_pair)]:
        instruments.loc[ticker, 'currency'] = ticker[3:6]  # the quote currency (as Yahoo reports it)
    prices = business_days(load_adjusted_closes(instruments.index.to_list()))
    instruments = instruments.loc[prices.columns]
    all_currencies = set(instruments['currency'].dropna())
    all_currencies |= {t[:3] for t in instruments.index if is_currency_pair(t)}
    rates = cash_rates(sorted(all_currencies), prices.index)
    for ticker in instruments.index[instruments.index.map(is_currency_pair)]:
        # the deposit in the base currency: the exchange rate times the interest accrued
        base = ticker[:3]
        prices[ticker] = prices[ticker] * deposit_growth(rates[base], DAYS_PER_YEAR.get(base, 360))
    return ResearchData(prices=prices, instruments=instruments, cash_rates=rates)
