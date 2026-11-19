"""Instruments of the Markets page: monthly returns of sectors, world indices, bonds, commodities, currencies and
crypto, each in its own currency. The instruments are saved in the database (table market_instruments): the list below
is only the starting data of a database without any.

Total returns: a total return index where Yahoo Finance has one (S&P 500 TR; the DAX is a total return index),
otherwise an ETF on the index, accumulating or with its distributions added back (adjusted close). Commodities are
front-month futures; currencies and bitcoin have no income.
"""

from typing import NamedTuple


class MarketInstrument(NamedTuple):
    """An instrument of the Markets page: its group (a block of rows), its label and its Yahoo Finance ticker."""

    group: str
    label: str
    ticker: str


DEFAULT_MARKET_INSTRUMENTS = [
    # MSCI World sectors (Xtrackers, EUR)
    MarketInstrument('Sectors', 'Financials', 'XDWF.DE'),
    MarketInstrument('Sectors', 'Industrials', 'XDWI.DE'),
    MarketInstrument('Sectors', 'Energy', 'XDW0.DE'),
    MarketInstrument('Sectors', 'Information Technology', 'XDWT.DE'),
    MarketInstrument('Sectors', 'Health Care', 'XDWH.DE'),
    MarketInstrument('Sectors', 'Utilities', 'XDWU.DE'),
    MarketInstrument('Sectors', 'Consumer Staples', 'XDWS.DE'),
    MarketInstrument('Sectors', 'Consumer Discretionary', 'XDWC.DE'),
    MarketInstrument('Sectors', 'Materials', 'XDWM.DE'),
    MarketInstrument('Sectors', 'Communication Services', 'XWTS.DE'),
    MarketInstrument('Sectors', 'Banks (Stoxx Europe 600)', 'EXV1.DE'),
    # world indices
    MarketInstrument('World indices', 'MSCI World', 'URTH'),
    MarketInstrument('World indices', 'MSCI Emerging Markets', 'EEM'),
    MarketInstrument('World indices', 'S&P 500', '^SP500TR'),
    MarketInstrument('World indices', 'Nasdaq 100', 'CNDX.L'),
    MarketInstrument('World indices', 'Euro Stoxx 50', 'SXRT.DE'),
    MarketInstrument('World indices', 'Stoxx Europe 600', 'MEUD.PA'),
    MarketInstrument('World indices', 'DAX', '^GDAXI'),
    MarketInstrument('World indices', 'SMI', 'CSSMI.SW'),
    MarketInstrument('World indices', 'FTSE 100', 'CUKX.L'),
    MarketInstrument('World indices', 'Nikkei 225', 'CSNKY.SW'),
    MarketInstrument('World indices', 'Hang Seng', '2800.HK'),
    # bonds
    MarketInstrument('Bonds', 'US Treasuries 7-10y', 'IEF'),
    MarketInstrument('Bonds', 'German Bunds 10.5y+', 'EXX6.DE'),
    # commodities (front-month futures, USD)
    MarketInstrument('Commodities', 'Gold', 'GC=F'),
    MarketInstrument('Commodities', 'Silver', 'SI=F'),
    MarketInstrument('Commodities', 'WTI crude oil', 'CL=F'),
    MarketInstrument('Commodities', 'Brent crude oil', 'BZ=F'),
    MarketInstrument('Commodities', 'Natural gas', 'NG=F'),
    MarketInstrument('Commodities', 'Copper', 'HG=F'),
    MarketInstrument('Commodities', 'Wheat', 'ZW=F'),
    # currencies
    MarketInstrument('Currencies', 'EUR/USD', 'EURUSD=X'),
    MarketInstrument('Currencies', 'USD/CHF', 'USDCHF=X'),
    MarketInstrument('Currencies', 'EUR/CHF', 'EURCHF=X'),
    MarketInstrument('Currencies', 'GBP/USD', 'GBPUSD=X'),
    MarketInstrument('Currencies', 'USD/JPY', 'USDJPY=X'),
    MarketInstrument('Currencies', 'US Dollar Index', 'DX-Y.NYB'),
    # crypto
    MarketInstrument('Crypto', 'Bitcoin', 'BTC-USD'),
]
