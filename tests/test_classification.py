import pandas as pd
import pytest

import portfolio_manager.analytics.classification as classification
import portfolio_manager.storage.queries as sql
from portfolio_manager.analytics.classification import AssetClass, classify
from portfolio_manager.analytics.subclasses import sub_classify


@pytest.mark.parametrize(
    'name, etp_type, allocation, expected',
    [
        # Degiro's allocation when it is a clear class
        ('iShares Core MSCI World UCITS ETF', 1, 'Shares', (AssetClass.EQUITY, False, 'degiro')),
        ('iShares Global Govt Bond UCITS ETF', 1, 'Bonds', (AssetClass.FIXED_INCOME, False, 'degiro')),
        ('Xtrackers II EUR Overnight Rate Swap', 1, 'Monetary', (AssetClass.MONEY_MARKET, False, 'degiro')),
        ('Vanguard LifeStrategy 60% Equity', 1, 'Mixed', (AssetClass.MULTI_ASSET, False, 'degiro')),
        # currency pairs, whatever Degiro says ("Derivatives")
        ('WisdomTree Long CHF Short EUR', 2, 'Derivatives', (AssetClass.ALTERNATIVES, False, 'name rule')),
        ('WisdomTree Short GBP Long EUR 3x Daily', 2, None, (AssetClass.ALTERNATIVES, True, 'name rule')),
        # the ETP type: crypto ETPs (even labelled Monetary), commodity ETCs
        ('21Shares Solana Staking ETP', 4, None, (AssetClass.CRYPTO, False, 'etp type')),
        ('Some Crypto ETN', 4, 'Monetary', (AssetClass.CRYPTO, False, 'etp type')),
        ('WisdomTree WTI Crude Oil ETC', 3, 'Derivatives', (AssetClass.COMMODITIES, False, 'etp type')),
        ('WisdomTree Copper 2X Daily Leveraged ETC', 3, None, (AssetClass.COMMODITIES, True, 'etp type')),
        # names, when Degiro has no clear class
        ('Invesco US Treasury Bond 10+ Year UCITS Acc ETF', 1, None, (AssetClass.FIXED_INCOME, False, 'name rule')),
        ('Wisdomtree Physical Crypto Altco ETP', None, 'Other', (AssetClass.CRYPTO, False, 'name rule')),
        ('SG Gold Future ETC/ETCN 1x', None, None, (AssetClass.COMMODITIES, False, 'name rule')),
        ('VanEck Multi-Asset Balanced Allocation UCITS ETF', None, None, (AssetClass.MULTI_ASSET, False, 'name rule')),
        (
            'iShares Developed Markets Property Yield UCITS ETF',
            None,
            None,
            (AssetClass.REAL_ESTATE, False, 'name rule'),
        ),
        ('Leverage Shares -3x Short Ferrari', None, None, (AssetClass.EQUITY, True, 'name rule')),
        # a fund named after a commodity, or after crypto, holds companies
        ('iShares STOXX Europe 600 Oil & Gas (DE)', None, None, (AssetClass.EQUITY, False, 'name rule')),
        ('VanEck Gold Miners UCITS ETF', None, None, (AssetClass.EQUITY, False, 'name rule')),
        ('CoinShares Bitcoin Mining UCITS ETF', 1, None, (AssetClass.EQUITY, False, 'name rule')),
        # equity strategies are not multi-asset; commodity index funds; inverse products named "short"
        (
            'UBS ETF (IE) US Equity Defensive Put Write SF UCITS ETF',
            None,
            None,
            (AssetClass.EQUITY, False, 'name rule'),
        ),
        ('Amundi Equity Global Multi Smart Allocation', None, None, (AssetClass.EQUITY, False, 'name rule')),
        ('UBS(Irl)FdSltnsplc Blmbg CommIdx SF U ETF HGBP Aa', None, None, (AssetClass.COMMODITIES, False, 'name rule')),
        ('BNP Paribas Henry Hub Natural ETC', None, None, (AssetClass.COMMODITIES, False, 'name rule')),
        ('Xtrackers ShortDAX Daily Swap UCITS ETF 1C', None, None, (AssetClass.EQUITY, True, 'name rule')),
        (
            'Xtrackers Short iBoxx EUR Sovereigns Eurozone Daily Swap',
            None,
            None,
            (AssetClass.FIXED_INCOME, True, 'name rule'),
        ),
        ('21Shares Short Ethereum ETP', 4, None, (AssetClass.CRYPTO, True, 'etp type')),
        ('Levshares -1X Apple Etp', None, None, (AssetClass.EQUITY, True, 'name rule')),
        # not leveraged: a 1x tracker, short-term bonds
        ('Leverage Shares 1x Amazon Tracker ETP', None, None, (AssetClass.EQUITY, False, 'name rule')),
        ('iShares USD Short Duration Corp Bond', 1, 'Bonds', (AssetClass.FIXED_INCOME, False, 'degiro')),
    ],
)
def test_asset_class_of_an_etf(name, etp_type, allocation, expected):
    assert classify(name, etp_type, allocation) == expected


def test_classification_of_the_catalog(temp_db, tmp_path, monkeypatch):
    catalog = pd.DataFrame(
        [
            (1, 'IE00B4L5Y983', 'iShares Core MSCI World UCITS ETF'),
            (2, 'IE00B4L5Y983', 'iShares Core MSCI World UCITS ETF'),  # another line of the same fund
            (3, 'JE00B1VS3770', 'WisdomTree Physical Gold'),
            (4, 'LU1681049109', 'Amundi Euro Government Bond 10-15Y'),  # missing from Degiro's list
        ],
        columns=['id', 'isin', 'name'],
    )
    monkeypatch.setattr(classification, 'catalog_products', lambda: catalog)
    monkeypatch.setattr(classification, 'RESULTS_DIR', str(tmp_path))
    etf_list = pd.DataFrame(
        [
            ('IE00B4L5Y983', 'Shares', 'Worldwide', 'MSCI World', 1),
            ('IE00B4L5Y983', '*none*', '*none*', '*none*', 1),  # a line without the data
            ('JE00B1VS3770', 'Derivatives', '*none*', '*none*', 3),
        ],
        columns=['isin', 'assetAllocation', 'region', 'benchmark', 'etpType'],
    )
    monkeypatch.setattr(classification, 'fetch_degiro_etf_list', lambda degiro_conn: etf_list)
    classification.classify_etfs(degiro_conn=None)
    classes = sql.query_etf_classes()
    assert classes['asset_class'].to_dict() == {
        'IE00B4L5Y983': 'Equity',
        'JE00B1VS3770': 'Commodities',
        'LU1681049109': 'Fixed Income',
    }
    assert classes['source'].to_dict() == {
        'IE00B4L5Y983': 'degiro',
        'JE00B1VS3770': 'etp type',
        'LU1681049109': 'name rule',
    }
    assert classes.loc['IE00B4L5Y983', ['degiro_region', 'degiro_benchmark']].tolist() == ['Worldwide', 'MSCI World']
    assert classes.loc['LU1681049109', 'degiro_allocation'] is None
    assert not classes['leveraged'].any()
    review = pd.read_excel(tmp_path / 'etf_classes.xlsx', sheet_name=None)
    assert set(review) == {'Classes', 'Summary', 'Sub-classes', 'Region sources'}
    # the sub-classes: Degiro's region of the equity fund, the market of the bonds from their name
    assert classes.loc['IE00B4L5Y983', ['sub_class', 'region', 'region_source']].tolist() == [
        'Broad',
        'Global',
        'degiro',
    ]
    assert classes.loc['LU1681049109', ['sub_class', 'region', 'region_source']].tolist() == [
        'Government',
        'Euro Area',
        'name',
    ]
    assert classes.loc['JE00B1VS3770', 'sub_class'] == 'Precious metals'


def test_degiro_etf_list_is_read_page_by_page(monkeypatch):
    monkeypatch.setattr(classification, 'ETF_LIST_PAGE', 2)
    etfs = [{'isin': f'XX000000000{n}', 'assetAllocation': 'Shares'} for n in range(5)]
    requests = []

    class Degiro:
        def product_search(self, product_request, raw):
            requests.append(product_request.offset)
            offset, limit = product_request.offset, product_request.limit
            return {'total': len(etfs), 'products': etfs[offset : offset + limit]}

    etf_list = classification.fetch_degiro_etf_list(Degiro())
    assert etf_list['isin'].tolist() == [e['isin'] for e in etfs] and requests == [0, 2, 4]


@pytest.mark.parametrize(
    'name, etp_type, allocation, benchmark, expected',
    [
        # property funds are Real Estate, even when Degiro counts them as shares
        ('iShares UK Property UCITS ETF GBP (Dist)', 1, 'Shares', None, AssetClass.REAL_ESTATE),
        ('Some Fund', 1, None, 'FTSE EPRA Nareit Developed Index', AssetClass.REAL_ESTATE),
        # an index without REITs holds no real estate
        ('Franklin LibertyQ Gl Dividend UCITS ETF', 1, 'Shares', 'MSCI AC World ex-REITS Index-NR', AssetClass.EQUITY),
        ('LGIM Managers (Europe) Limited', 1, None, 'FTSE All Cap ex CW ex TC ex REITS Dividend', AssetClass.EQUITY),
        # abbreviated bond names
        ('SPDR Bloomberg Barclays Glo Aggrt Bd ETF USDH Acc', None, None, None, AssetClass.FIXED_INCOME),
        ('WISDOMTREE USD FLOAT T', None, None, None, AssetClass.FIXED_INCOME),
        # funds holding a metal, energy ETCs, multi-asset funds naming their equity share
        ('ZKB Gold ETF AAH EUR', None, None, None, AssetClass.COMMODITIES),
        ('BNP PARIBAS NY Harbor ULSD ETC', None, None, None, AssetClass.COMMODITIES),
        ('RICI Enhanced Energy Total Return Index ETC', None, None, None, AssetClass.COMMODITIES),
        ('VanEck Oil Services UCITS ETF', None, None, None, AssetClass.EQUITY),
        ('Vanguard LifeStrategy 20% Equity UCITS ETF EUR Acc', None, None, None, AssetClass.MULTI_ASSET),
        # a name that is only the issuer: the benchmark tells
        ('DWS Investment S.A.', None, None, 'Bloomberg Euro Treasury Index', AssetClass.FIXED_INCOME),
    ],
)
def test_asset_class_from_the_benchmark_and_property_funds(name, etp_type, allocation, benchmark, expected):
    assert classify(name, etp_type, allocation, benchmark)[0] == expected


@pytest.mark.parametrize(
    'asset_class, name, benchmark, degiro_region, expected',
    [
        # equities: Degiro's region first, otherwise the benchmark, otherwise the name; the segment
        ('Equity', 'iShares Core MSCI World UCITS ETF', 'MSCI World', 'Worldwide', ('Broad', 'Global', 'degiro')),
        ('Equity', 'Xtrackers DAX UCITS ETF 1C', 'Dax', None, ('Broad', 'Euro Area', 'benchmark')),
        ('Equity', 'iShares SMI ETF (CH)', None, None, ('Broad', 'Switzerland', 'name')),
        ('Equity', 'iShares Core S&P 500 UCITS ETF', None, None, ('Broad', 'North America', 'name')),
        ('Equity', 'Xtrackers MSCI World ex USA UCITS ETF', None, None, ('Broad', 'Global', 'name')),
        ('Equity', 'iShares MSCI AC Far East ex-Jpn UCITS ETF', None, None, ('Broad', 'Asia Pacific', 'name')),
        ('Equity', 'Invesco STOXX Europe 600 Optimised Banks ETF', None, None, ('Sector', 'Europe', 'name')),
        ('Equity', 'iShares Global Clean Energy UCITS ETF', None, None, ('Thematic', 'Global', 'name')),
        ('Equity', 'Vanguard FTSE All-World High Dividend Yield', None, None, ('Factor', 'Global', 'name')),
        ('Equity', 'iShares MSCI China UCITS ETF', None, None, ('Broad', 'Emerging Markets', 'name')),
        ('Equity', 'Amundi SDAX UCITS ETF Dist', None, None, ('Broad', 'Euro Area', 'name')),
        ('Equity', 'First Trust Dow Jones Intl Internet U ETF', None, None, ('Thematic', 'Global', 'name')),
        # bonds: the market of the bonds held, not the currency the fund trades in or is hedged to
        (
            'Fixed Income',
            'iShares $ Treasury Bond 7-10yr UCITS ETF EUR',
            None,
            None,
            ('Government', 'United States', 'name'),
        ),
        ('Fixed Income', 'iShares Euro Corporate Bond UCITS ETF', None, None, ('Corporate', 'Euro Area', 'name')),
        (
            'Fixed Income',
            'Vanguard Global Aggregate Bond UCITS ETF CHF Hedged',
            'Bloomberg Global Aggregate Float Adjusted Index',
            None,
            ('Aggregate', 'Global', 'benchmark'),
        ),
        ('Fixed Income', 'iShares USD TIPS UCITS ETF', None, None, ('Inflation-linked', 'United States', 'name')),
        ('Fixed Income', 'iShares Euro High Yield Corp Bond', None, None, ('High Yield', 'Euro Area', 'name')),
        ('Fixed Income', 'Xtrackers II US Treasuries 0-1', None, None, ('Short-term', 'United States', 'name')),
        ('Fixed Income', 'iShares Emerging Markets Govt Bond', None, None, ('Government', 'Emerging Markets', 'name')),
        # the other classes
        ('Commodities', 'WisdomTree Physical Gold', None, None, ('Precious metals', None, None)),
        ('Commodities', 'WisdomTree Brent Crude Oil', None, None, ('Energy', None, None)),
        ('Commodities', 'UBS CMCI Composite SF ETF', None, None, ('Broad', None, None)),
        ('Real Estate', 'iShares European Property Yield', None, None, (None, 'Europe', 'name')),
        ('Money Market', 'Xtrackers II EUR Overnight Rate Swap', None, None, (None, 'Euro Area', 'name')),
        ('Crypto', 'CoinShares Physical Bitcoin', None, None, ('Bitcoin', None, None)),
        ('Crypto', 'WisdomTree Physical Crypto Altcoins', None, None, ('Basket', None, None)),
        ('Crypto', '21Shares Solana Staking ETP', None, None, ('Other coin', None, None)),
    ],
)
def test_sub_class_of_an_etf(asset_class, name, benchmark, degiro_region, expected):
    sub = sub_classify(asset_class, name, benchmark, degiro_region)
    assert (sub.sub_class, sub.region, sub.region_source) == expected


@pytest.mark.parametrize(
    'name, expected',
    [
        ('iShares MSCI World CHF Hedged UCITS ETF (Acc)', (True, 'CHF')),
        ('Xtrackers MSCI EMU UCITS ETF 3C - CHF Hedged', (True, 'CHF')),
        ('UBS(Irl)FdSltnsplc BB Comdt CMCI SF U ETF (CHFh)Aa', (True, 'CHF')),
        ('UBS(Irl)FdSltnsplc Blmbg CommIdx SF U ETF HEUR Aa', (True, 'EUR')),
        ('Vanguard Global Aggregate Bond UCITS ETF (hedged to EUR)', (True, 'EUR')),
        ('iShares Core MSCI World UCITS ETF USD (Acc)', (False, None)),
    ],
)
def test_hedged_share_classes(name, expected):
    sub = sub_classify('Equity', name, None, None)
    assert (sub.hedged, sub.hedge_currency) == expected
