"""Asset class of the ETFs of the catalog (Equity, Fixed Income, Money Market, Commodities, Real Estate, Crypto,
Multi-Asset, Alternatives) and whether they are leveraged or inverse, one per ISIN.

Degiro's ETF list gives an asset allocation per ETF, but not for every ETF (many ETCs, ETNs and crypto ETPs have
none, and many funds are missing from the list), and some of its classes mix unrelated products ("Derivatives":
currency pairs, commodity ETCs and leveraged notes; "Other"). The class comes from, in order: a currency pair in the
name, the ETP type (crypto ETP, commodity ETC), Degiro's allocation when it is a clear class, rules on the name.
"""

import logging
import re
import time
from datetime import datetime
from enum import Enum

import pandas as pd
from degiro_connector.trading.api import API
from degiro_connector.trading.models.product_search import ETFsRequest

from portfolio_manager.analytics.instruments import catalog_products
from portfolio_manager.analytics.subclasses import EXCLUSION, sub_classify
from portfolio_manager.config.settings import RESULTS_DIR
from portfolio_manager.storage.files import save_df_dict_to_excel
from portfolio_manager.storage.queries import save_etf_classes

logger = logging.getLogger(__name__)

ETF_LIST_PAGE = 500  # ETFs per request of Degiro's ETF list
CLASSES_FILE = 'etf_classes'  # Excel copy of the classification, for review


class AssetClass(str, Enum):
    """Macro asset classes."""

    EQUITY = 'Equity'
    FIXED_INCOME = 'Fixed Income'
    MONEY_MARKET = 'Money Market'
    COMMODITIES = 'Commodities'
    REAL_ESTATE = 'Real Estate'
    CRYPTO = 'Crypto'
    MULTI_ASSET = 'Multi-Asset'
    ALTERNATIVES = 'Alternatives'


class EtpType(int, Enum):
    """Degiro's ETP types (from the names of the products of each type)."""

    ETF = 1
    NOTE = 2  # currency pairs, leveraged notes, some crypto
    ETC = 3  # commodities
    CRYPTO = 4


# Degiro asset allocations that are a clear class (the others: Derivatives, Other, Performance Swaps, none)
DEGIRO_CLASSES = {
    'Shares': AssetClass.EQUITY,
    'Bonds': AssetClass.FIXED_INCOME,
    'Convertible bonds': AssetClass.FIXED_INCOME,
    'Monetary': AssetClass.MONEY_MARKET,
    'Commodities': AssetClass.COMMODITIES,
    'Real Estate': AssetClass.REAL_ESTATE,
    'Mixed': AssetClass.MULTI_ASSET,
    'Crypto': AssetClass.CRYPTO,
}
NO_VALUE = '*none*'  # Degiro's value of a field it does not know


def words(*patterns: str) -> re.Pattern:
    """Pattern matching any of the words or phrases (case ignored, whole words)."""
    return re.compile(r'\b(?:' + '|'.join(patterns) + r')\b', re.IGNORECASE)


CURRENCY_PAIR = re.compile(r'\b(?:long|short) [A-Z]{3} (?:long|short) [A-Z]{3}\b', re.IGNORECASE)
LEVERAGED = words(
    r'-?[2-9](?:\.\d)?x',
    r'-1x',
    r'\d+x daily',
    'leveraged',
    'inverse',
    'daily short',
    'short daily',
    'bear',
    'shortdax',
)
MINUS_ONE = re.compile(r'(?<!\w)-1x(?!\w)', re.IGNORECASE)  # inverse (-1x: no word boundary before the sign)
# "short" makes a product inverse only with a sign of a daily product (not e.g. "Short Duration" bonds)
SHORT = words('short')
SHORT_PRODUCT = words('daily', 'etp', 'etn')
COMPANIES = words(
    'miners?', 'mining', 'producers?', 'companies', 'equity', 'equities', 'stocks?', 'shares', 'blockchain'
)
NAME_RULES = [  # the first matching rule gives the class
    (
        AssetClass.CRYPTO,
        words(
            'bitcoin',
            'btc',
            'ethereum',
            'ether',
            'crypto',
            'solana',
            'polkadot',
            'cardano',
            'xrp',
            'ripple',
            'litecoin',
            'chainlink',
            'avalanche',
            'tezos',
            'algorand',
            'cosmos',
            'staking',
            'tron',
            'dogecoin',
            'polygon',
            'aave',
            'uniswap',
            'coindesk',
            'altcoins?',
        ),
    ),
    (AssetClass.MONEY_MARKET, words('money market', 'overnight', '€str', 'estr', 'sonia', 'sofr', 'saron', 'eonia')),
    (AssetClass.REAL_ESTATE, words('real estate', 'reits?', 'property', 'properties', 'epra', 'nareit')),
    (
        AssetClass.MULTI_ASSET,
        words('multi-asset', 'multi asset', 'allocation', 'balanced', 'defensive', 'conservative'),
    ),
    (
        AssetClass.FIXED_INCOME,
        words(
            'bonds?',
            'treasury',
            'treasuries',
            'govt',
            'government',
            'gilts?',
            'corp',
            'corporate',
            'aggregate',
            'tips',
            'inflation',
            'high yield',
            'float(?:ing)? rate',
            'fixed income',
            'sovereign',
            't-bills?',
            'bunds?',
            'btp',
            'oat',
            'iboxx',
            'covered bonds?',
            'debt',
            'fallen angels',
            'yield curve',
            'bd',  # abbreviated names, e.g. "Glo Aggrt Bd", "USD Float T"
            'aggrt',
            'float t',
        ),
    ),
]
# names of commodities count only for a product that is not a fund (an ETC, ETN, ETP or future), since funds named after
# one usually hold companies (e.g. an "Oil & Gas" sector fund)
COMMODITY_PRODUCT = words(
    'etc',
    'etcs',
    'etn',
    'etp',
    'futures?',
    'physical',
    'commodit(?:y|ies)',
    'commidx',
    'cmci',
    'bcom',
    '(?:gold|silver|platinum|palladium) etfs?',  # funds holding the metal, e.g. "ZKB Gold ETF"
)
# names of funds mixing asset classes, also when they name their equity share (e.g. "LifeStrategy 20% Equity")
MULTI_ASSET_FUNDS = words('lifestrategy', r'\d+% equity', 'multi-asset', 'multi asset')
COMMODITY_NAMES = words(
    'gold',
    'silver',
    'platinum',
    'palladium',
    'precious metals?',
    'oil',
    'brent',
    'wti',
    'crude',
    'natural gas',
    'gas',
    'copper',
    'nickel',
    'aluminium',
    'aluminum',
    'zinc',
    'lead',
    'tin',
    'wheat',
    'corn',
    'soybeans?',
    'sugar',
    'coffee',
    'cocoa',
    'cotton',
    'livestock',
    'cattle',
    'hogs',
    'commodit(?:y|ies)',
    'industrial metals',
    'agriculture',
    'carbon',
    'eua',
    'grains',
    'rhodium',
    'henry hub',
    'ulsd',  # ultra-low sulphur diesel
    'diesel',
    'heating oil',
    'energy',  # only for products, e.g. "RICI Enhanced Energy ETC" (not an energy sector fund)
    'commidx',  # commodity indices: Bloomberg (BCOM), UBS CMCI
    'cmci',
    'bcom',
)


def classify_name(name: str, etp_type: int | None) -> AssetClass | None:
    """Asset class named in a text (an ETF name or benchmark), None if none is."""
    if MULTI_ASSET_FUNDS.search(name):
        return AssetClass.MULTI_ASSET
    holds_companies = bool(COMPANIES.search(name))  # e.g. gold miners, blockchain companies, equity strategies
    for asset_class, pattern in NAME_RULES:
        if pattern.search(name) and not (
            asset_class in (AssetClass.CRYPTO, AssetClass.MULTI_ASSET) and holds_companies
        ):
            return asset_class
    is_product = etp_type in (EtpType.NOTE, EtpType.ETC) or bool(COMMODITY_PRODUCT.search(name))
    if COMMODITY_NAMES.search(name) and is_product and not holds_companies:
        return AssetClass.COMMODITIES
    return None


def classify(
    name: str | None, etp_type: int | None, allocation: str | None, benchmark: str | None = None
) -> tuple[AssetClass, bool, str]:
    """Asset class of an ETF, whether it is leveraged or inverse, and the source of the class (degiro, etp type, name
    rule), from its name, its Degiro ETP type, its Degiro asset allocation and its benchmark (None if unknown).
    """
    name, benchmark = name or '', benchmark or ''
    leveraged = bool(
        LEVERAGED.search(name) or MINUS_ONE.search(name) or (SHORT.search(name) and SHORT_PRODUCT.search(name))
    )
    if CURRENCY_PAIR.search(name):
        return AssetClass.ALTERNATIVES, leveraged, 'name rule'
    if etp_type == EtpType.CRYPTO:
        return AssetClass.CRYPTO, leveraged, 'etp type'
    if etp_type == EtpType.ETC:
        return AssetClass.COMMODITIES, leveraged, 'etp type'
    # without exclusions (e.g. a dividend index "ex REITs" holds no real estate)
    name, benchmark = EXCLUSION.sub(' ', name), EXCLUSION.sub(' ', benchmark)
    # funds of real estate companies and REITs, which Degiro counts as shares
    real_estate = dict(NAME_RULES)[AssetClass.REAL_ESTATE]
    if allocation in (None, 'Shares') and (real_estate.search(name) or real_estate.search(benchmark)):
        return AssetClass.REAL_ESTATE, leveraged, 'name rule'
    if allocation in DEGIRO_CLASSES:
        return DEGIRO_CLASSES[allocation], leveraged, 'degiro'
    # the name, then the benchmark (some Degiro names are only the issuer, e.g. "DWS Investment S.A.")
    asset_class = classify_name(name, etp_type) or classify_name(benchmark, etp_type)
    return asset_class or AssetClass.EQUITY, leveraged, 'name rule'


def fetch_degiro_etf_list(degiro_conn: API) -> pd.DataFrame:
    """Degiro's ETF list: one row per ETF line (product), with the fields Degiro returns (isin, name, assetAllocation,
    region, benchmark, etpType, ...).
    """
    products, total, offset = [], None, 0
    while total is None or offset < total:
        request = ETFsRequest(
            popular_only=False,
            input_aggregate_types='',
            input_aggregate_values='',
            search_text='',
            offset=offset,
            limit=ETF_LIST_PAGE,
            require_total=True,
            sort_columns='name',
            sort_types='asc',
        )
        page = None
        for attempt in range(3):  # a lost answer is asked again
            page = degiro_conn.product_search(product_request=request, raw=True)
            if page is not None:
                break
            time.sleep(2 * (attempt + 1))
        if page is None:
            raise RuntimeError(f'No answer from Degiro for its ETF list (offset {offset})')
        total, batch = page.get('total', 0), page.get('products') or []
        if not batch:
            break
        products += batch
        offset += len(batch)
    logger.info('Degiro ETF list: %d ETF lines', len(products))
    return pd.DataFrame(products)


def _known(values: pd.Series):
    # the most frequent known value of a field among the lines of a fund, None if none is known
    values = values[values.notna() & (values != NO_VALUE)]
    return values.mode().iloc[0] if not values.empty else None


def classify_catalog(etf_list: pd.DataFrame) -> pd.DataFrame:
    """Asset class of every fund (ISIN) of the catalog: one row per ISIN with the columns of the etf_classes table."""
    products = catalog_products().dropna(subset=['isin']).drop_duplicates('isin')
    columns = {
        'assetAllocation': 'degiro_allocation',
        'region': 'degiro_region',
        'benchmark': 'degiro_benchmark',
        'etpType': 'degiro_etp_type',
    }
    degiro = etf_list.groupby('isin')[list(columns)].agg(_known).rename(columns=columns) if not etf_list.empty else None
    rows = []
    for product in products.itertuples():
        info = degiro.loc[product.isin].to_dict() if degiro is not None and product.isin in degiro.index else {}
        etp_type = int(info['degiro_etp_type']) if info.get('degiro_etp_type') is not None else None
        benchmark = info.get('degiro_benchmark')
        asset_class, leveraged, source = classify(product.name, etp_type, info.get('degiro_allocation'), benchmark)
        sub = sub_classify(asset_class.value, product.name, benchmark, info.get('degiro_region'))
        rows.append(
            {
                'isin': product.isin,
                'name': product.name,
                'asset_class': asset_class.value,
                'leveraged': leveraged,
                'source': source,
                'sub_class': sub.sub_class,
                'region': sub.region,
                'region_source': sub.region_source,
                'hedged': sub.hedged,
                'hedge_currency': sub.hedge_currency,
                'degiro_allocation': info.get('degiro_allocation'),
                'degiro_region': info.get('degiro_region'),
                'degiro_benchmark': info.get('degiro_benchmark'),
                'degiro_etp_type': etp_type,
                'classified_at': datetime.now(),
            }
        )
    return pd.DataFrame(rows)


def classify_etfs(degiro_conn: API) -> pd.DataFrame:
    """Classify the ETFs of the catalog from Degiro's ETF list (needs a Degiro connection), save the classes (table
    etf_classes) and an Excel copy for review: the classes, their count by class and source, by sub-class and region,
    and the sources of the regions.
    """
    return save_classes(classify_catalog(fetch_degiro_etf_list(degiro_conn)))


def save_classes(classes: pd.DataFrame) -> pd.DataFrame:
    """Save the classes of the ETFs (table etf_classes) and their Excel copy for review (see classify_etfs)."""
    save_etf_classes(classes)
    summary = pd.crosstab(classes['asset_class'], classes['source'], margins=True, margins_name='Total')
    summary['leveraged'] = classes.groupby('asset_class')['leveraged'].sum().reindex(summary.index)
    sub_classes = classes.fillna({'sub_class': '-', 'region': '(unknown)'})
    by_sub_class = pd.crosstab([sub_classes['asset_class'], sub_classes['sub_class']], sub_classes['region'])
    region_sources = pd.crosstab(classes['asset_class'], classes['region_source'].fillna('(none)'))
    sheets = {
        'Classes': classes.drop(columns='classified_at').set_index('isin'),
        'Summary': summary,
        'Sub-classes': by_sub_class,
        'Region sources': region_sources,
    }
    save_df_dict_to_excel(df_dict=sheets, file_name=CLASSES_FILE, folder_name=RESULTS_DIR)
    logger.info('ETF classes saved: %d funds (%s)', len(classes), classes['asset_class'].value_counts().to_dict())
    return classes
