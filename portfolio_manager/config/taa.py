"""Instruments and interest rates of the Tactical Asset Allocation research: equity indices, sectors, bonds,
commodities and currencies (Yahoo Finance listings, their whole history), and the interest rates of the main
currencies (central banks, the BIS and Yahoo Finance).

Total returns: a total return index where Yahoo Finance has one (S&P 500 TR, SPI), otherwise an ETF on the index,
accumulating or with its distributions stored (adjusted close). Commodities are investable funds, which include the cost
of rolling the futures. Instruments with long histories are preferred: strategies are tested over many years.

The instruments and the rate series are saved in the database (tables taa_instruments and taa_rate_series): the lists
below are only the starting data of a database without any.
"""

from typing import NamedTuple


class TaaInstrument(NamedTuple):
    """An instrument of the research: its group, its label, its Yahoo Finance ticker, and whether it is in use (a paused
    instrument keeps its stored data but is no longer updated).
    """

    group: str
    label: str
    ticker: str
    active: bool = True


DEFAULT_TAA_INSTRUMENTS = [
    # equity indices
    TaaInstrument('Equity indices', 'S&P 500 (TR)', '^SP500TR'),
    TaaInstrument('Equity indices', 'Nasdaq 100', 'QQQ'),
    TaaInstrument('Equity indices', 'SPI (TR)', '^SSHI'),
    TaaInstrument('Equity indices', 'Euro Stoxx 50', 'EXW1.DE'),
    TaaInstrument('Equity indices', 'Stoxx Europe 600', 'EXSA.DE'),
    TaaInstrument('Equity indices', 'MSCI World', 'IWDA.AS'),
    TaaInstrument('Equity indices', 'MSCI Emerging Markets', 'EEM'),
    TaaInstrument('Equity indices', 'MSCI Japan', 'EWJ'),
    # US sectors (S&P 500 sectors)
    TaaInstrument('US sectors', 'Information Technology', 'XLK'),
    TaaInstrument('US sectors', 'Financials', 'XLF'),
    TaaInstrument('US sectors', 'Energy', 'XLE'),
    TaaInstrument('US sectors', 'Health Care', 'XLV'),
    TaaInstrument('US sectors', 'Industrials', 'XLI'),
    TaaInstrument('US sectors', 'Consumer Discretionary', 'XLY'),
    TaaInstrument('US sectors', 'Consumer Staples', 'XLP'),
    TaaInstrument('US sectors', 'Utilities', 'XLU'),
    TaaInstrument('US sectors', 'Materials', 'XLB'),
    TaaInstrument('US sectors', 'Real Estate', 'XLRE'),
    TaaInstrument('US sectors', 'Communication Services', 'XLC'),
    # European sectors (Stoxx Europe 600 supersectors)
    TaaInstrument('European sectors', 'Banks', 'EXV1.DE'),
    TaaInstrument('European sectors', 'Oil & Gas', 'EXH1.DE'),
    TaaInstrument('European sectors', 'Technology', 'EXV3.DE'),
    TaaInstrument('European sectors', 'Health Care', 'EXV4.DE'),
    TaaInstrument('European sectors', 'Food & Beverage', 'EXH3.DE'),
    TaaInstrument('European sectors', 'Telecommunications', 'EXV2.DE'),
    TaaInstrument('European sectors', 'Utilities', 'EXH9.DE'),
    TaaInstrument('European sectors', 'Industrial Goods & Services', 'EXH4.DE'),
    TaaInstrument('European sectors', 'Basic Resources', 'EXV6.DE'),
    TaaInstrument('European sectors', 'Personal & Household Goods', 'EXH7.DE'),
    TaaInstrument('European sectors', 'Automobiles & Parts', 'EXV5.DE'),
    TaaInstrument('European sectors', 'Insurance', 'EXH5.DE'),
    TaaInstrument('European sectors', 'Real Estate', 'EXI5.DE'),
    TaaInstrument('European sectors', 'Construction & Materials', 'EXV8.DE'),
    TaaInstrument('European sectors', 'Financial Services', 'EXH2.DE'),
    TaaInstrument('European sectors', 'Media', 'EXH6.DE'),
    TaaInstrument('European sectors', 'Travel & Leisure', 'EXV9.DE'),
    TaaInstrument('European sectors', 'Retail', 'EXH8.DE'),
    TaaInstrument('European sectors', 'Chemicals', 'EXV7.DE'),
    # bonds
    TaaInstrument('Bonds', 'US Treasuries 1-3y', 'SHY'),
    TaaInstrument('Bonds', 'US Treasuries 7-10y', 'IEF'),
    TaaInstrument('Bonds', 'US Treasuries 20y+', 'TLT'),
    TaaInstrument('Bonds', 'US inflation-linked', 'TIP'),
    TaaInstrument('Bonds', 'US corporate', 'LQD'),
    TaaInstrument('Bonds', 'US high yield', 'HYG'),
    TaaInstrument('Bonds', 'Emerging markets (USD)', 'EMB'),
    TaaInstrument('Bonds', 'German Bunds 10.5y+', 'EXX6.DE'),
    TaaInstrument('Bonds', 'Euro government 15-30y', 'IBGL.AS'),
    # commodities (investable funds, USD)
    TaaInstrument('Commodities', 'Gold', 'GLD'),
    TaaInstrument('Commodities', 'Silver', 'SLV'),
    TaaInstrument('Commodities', 'Broad commodities', 'DBC'),
    TaaInstrument('Commodities', 'Crude oil', 'USO'),
    TaaInstrument('Commodities', 'Agriculture', 'DBA'),
    # currencies
    TaaInstrument('Currencies', 'EUR/USD', 'EURUSD=X'),
    TaaInstrument('Currencies', 'GBP/USD', 'GBPUSD=X'),
    TaaInstrument('Currencies', 'USD/JPY', 'USDJPY=X'),
    TaaInstrument('Currencies', 'USD/CHF', 'USDCHF=X'),
    TaaInstrument('Currencies', 'EUR/CHF', 'EURCHF=X'),
    TaaInstrument('Currencies', 'AUD/USD', 'AUDUSD=X'),
    TaaInstrument('Currencies', 'USD/CAD', 'USDCAD=X'),
    TaaInstrument('Currencies', 'NZD/USD', 'NZDUSD=X'),
    TaaInstrument('Currencies', 'USD/SEK', 'USDSEK=X'),
    TaaInstrument('Currencies', 'USD/NOK', 'USDNOK=X'),
]


class RateSources:
    """Sources of the interest rates."""

    ECB = 'ECB'  # European Central Bank data portal: code = 'dataflow/series key'
    SNB = 'SNB'  # Swiss National Bank data portal: code = 'cube/item'
    BOE = 'BoE'  # Bank of England database: code = series code
    YAHOO = 'Yahoo'  # Yahoo Finance index of the yield: code = ticker
    BIS = 'BIS'  # Bank for International Settlements data portal: code = 'dataflow/series key'


class RateSeries(NamedTuple):
    """A daily interest rate series: its key in the database, label, currency, maturity, source and its code there, and
    whether it is in use (a paused series keeps its stored values but is no longer updated).
    """

    key: str
    label: str
    currency: str
    tenor: str
    source: str
    code: str
    active: bool = True


ECB_AAA_YIELD = 'YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_'  # spot yields of the euro area AAA government bonds
DEFAULT_RATE_SERIES = [
    # EUR: the euro short-term rate since October 2019, EONIA before (EONIA = euro short-term rate + 0.085% from
    # October 2019 to its end in January 2022); euro area AAA government spot yields
    RateSeries('EUR_ESTR', 'Euro short-term rate (€STR)', 'EUR', 'O/N', RateSources.ECB, 'EST/B.EU000A2X2A25.WT'),
    RateSeries('EUR_EONIA', 'EONIA', 'EUR', 'O/N', RateSources.ECB, 'EON/D.EONIA_TO.RATE'),
    RateSeries('EUR_GOV_3M', 'Euro area AAA government 3M', 'EUR', '3M', RateSources.ECB, ECB_AAA_YIELD + '3M'),
    RateSeries('EUR_GOV_2Y', 'Euro area AAA government 2Y', 'EUR', '2Y', RateSources.ECB, ECB_AAA_YIELD + '2Y'),
    RateSeries('EUR_GOV_5Y', 'Euro area AAA government 5Y', 'EUR', '5Y', RateSources.ECB, ECB_AAA_YIELD + '5Y'),
    RateSeries('EUR_GOV_10Y', 'Euro area AAA government 10Y', 'EUR', '10Y', RateSources.ECB, ECB_AAA_YIELD + '10Y'),
    RateSeries('EUR_GOV_30Y', 'Euro area AAA government 30Y', 'EUR', '30Y', RateSources.ECB, ECB_AAA_YIELD + '30Y'),
    # CHF: SARON and its 3-month compound rate, Swiss Confederation spot yields (daily values, published monthly)
    RateSeries('CHF_SARON', 'SARON', 'CHF', 'O/N', RateSources.SNB, 'zirepo/H0'),
    RateSeries('CHF_SARON_3M', 'SARON 3M compound rate', 'CHF', '3M', RateSources.SNB, 'zirepo/H7'),
    RateSeries('CHF_GOV_2Y', 'Swiss Confederation 2Y', 'CHF', '2Y', RateSources.SNB, 'rendeiduebd/CHF/2J'),
    RateSeries('CHF_GOV_5Y', 'Swiss Confederation 5Y', 'CHF', '5Y', RateSources.SNB, 'rendeiduebd/CHF/5J'),
    RateSeries('CHF_GOV_10Y', 'Swiss Confederation 10Y', 'CHF', '10Y', RateSources.SNB, 'rendeiduebd/CHF/10J'),
    # GBP: SONIA, gilt par yields
    RateSeries('GBP_SONIA', 'SONIA', 'GBP', 'O/N', RateSources.BOE, 'IUDSOIA'),
    RateSeries('GBP_GOV_5Y', 'UK gilts 5Y', 'GBP', '5Y', RateSources.BOE, 'IUDSNPY'),
    RateSeries('GBP_GOV_10Y', 'UK gilts 10Y', 'GBP', '10Y', RateSources.BOE, 'IUDMNPY'),
    # USD: Treasury bill and bond yields (CBOE indices on Yahoo Finance)
    RateSeries('USD_TBILL_3M', 'US Treasury bills 13 weeks', 'USD', '3M', RateSources.YAHOO, '^IRX'),
    RateSeries('USD_GOV_5Y', 'US Treasuries 5Y', 'USD', '5Y', RateSources.YAHOO, '^FVX'),
    RateSeries('USD_GOV_10Y', 'US Treasuries 10Y', 'USD', '10Y', RateSources.YAHOO, '^TNX'),
    RateSeries('USD_GOV_30Y', 'US Treasuries 30Y', 'USD', '30Y', RateSources.YAHOO, '^TYX'),
    # other currencies of the currency pairs: central bank policy rates (BIS), the cash rate of the currency
    *[
        RateSeries(
            f'{currency}_POLICY', f'{bank} policy rate', currency, 'Policy', RateSources.BIS, f'WS_CBPOL/D.{area}'
        )
        for currency, area, bank in [
            ('JPY', 'JP', 'Bank of Japan'),
            ('AUD', 'AU', 'Reserve Bank of Australia'),
            ('CAD', 'CA', 'Bank of Canada'),
            ('NZD', 'NZ', 'Reserve Bank of New Zealand'),
            ('SEK', 'SE', 'Sveriges Riksbank'),
            ('NOK', 'NO', 'Norges Bank'),
        ]
    ],
]
