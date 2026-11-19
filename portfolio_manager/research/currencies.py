"""Currencies of the research: the excess return of a deposit in one currency valued in another (over the cash of the
second one), built from the stored currency pairs, and the interest rate gap between the two (the carry).

Every pair of the research has the US dollar on one side: a pair XXXUSD gives XXX against the dollar, a pair USDXXX
inverted gives it too, and two currencies are crossed through the dollar when no pair links them directly (to first
order, the excess returns of the two legs add up).
"""

import pandas as pd

from portfolio_manager.research.returns import ResearchData

ANCHOR = 'USD'


def excess_index(excess_returns: pd.DataFrame) -> pd.DataFrame:
    """Index of excess returns (1 before the first return), unknown before the first known return of each column."""
    return (1.0 + excess_returns.fillna(0.0)).cumprod().where(excess_returns.notna().cummax())


def pair_ticker(base: str, quote: str) -> str:
    """Yahoo Finance ticker of a currency pair."""
    return f'{base}{quote}=X'


class CurrencyIndices:
    """Excess return indices of currency deposits valued in other currencies, from the pairs of research data."""

    def __init__(self, data: ResearchData):
        self.data = data
        pairs = [t for t in data.prices.columns if t.endswith('=X')]
        self.pairs = excess_index(data.excess_returns('B')[pairs])

    def currencies(self) -> list[str]:
        """The currencies with a pair against the dollar, the dollar included."""
        found = {ANCHOR}
        for ticker in self.pairs.columns:
            base, quote = ticker[:3], ticker[3:6]
            if ANCHOR in (base, quote):
                found.add(quote if base == ANCHOR else base)
        return sorted(found)

    def index(self, currency: str, home: str) -> pd.Series:
        """Excess return index of a deposit in currency valued in home (over the cash of home)."""
        if currency == home:
            raise ValueError('A currency against itself has no excess return')
        direct, inverse = pair_ticker(currency, home), pair_ticker(home, currency)
        if direct in self.pairs:
            return self.pairs[direct].rename(f'{currency}/{home}')
        if inverse in self.pairs:
            return (1.0 / self.pairs[inverse]).rename(f'{currency}/{home}')
        if ANCHOR in (currency, home):
            raise KeyError(f'No currency pair for {currency}/{home}')
        return (self.index(currency, ANCHOR) / self.index(home, ANCHOR)).rename(f'{currency}/{home}')

    def excess_returns(self, currencies: list[str], home: str) -> pd.DataFrame:
        """Daily excess returns of deposits in currencies valued in home (columns: the currencies)."""
        indices = pd.DataFrame({c: self.index(c, home) for c in currencies if c != home})
        return indices.pct_change(fill_method=None)

    def carry(self, currencies: list[str], home: str) -> pd.DataFrame:
        """Interest rate gap of each currency over home (decimals per year), what an unhedged deposit earns."""
        rates = self.data.cash_rates
        return pd.DataFrame({c: rates[c] - rates[home] for c in currencies if c != home})
