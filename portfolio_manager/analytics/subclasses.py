"""Sub-classes of the ETFs of the catalog, within their macro asset class (see classification):

- Equity: region (Global, North America, Europe, Euro Area, Switzerland, UK, Japan, Asia Pacific, Emerging Markets,
  Other) and segment (Broad, Sector, Thematic, Factor);
- Fixed Income: type (Government, Corporate, High Yield, Inflation-linked, Aggregate, Short-term, Other) and region,
  the market of the bonds held (by the currency they are issued in: Euro Area for euro bonds, United States for dollar
  bonds, Switzerland, UK, Global, Emerging Markets), never the currency the fund trades in;
- Commodities: Precious metals, Energy, Industrial metals, Agriculture, Broad;
- Real Estate: region; Money Market: region (the currency market); Crypto: Bitcoin, Ethereum, Basket, Other coin;

and whether the share class is currency hedged (hedge currency). The region comes from Degiro's region when it has one
(equities), otherwise from the benchmark, otherwise from the name; the other sub-classes from the benchmark and name.
"""

import re
from typing import NamedTuple


def words(*patterns: str) -> re.Pattern:
    """Pattern matching any of the words or phrases (case ignored, whole words)."""
    return re.compile(r'(?<!\w)(?:' + '|'.join(patterns) + r')(?!\w)', re.IGNORECASE)


class Region:
    """Regions of equities and bond markets."""

    GLOBAL = 'Global'
    NORTH_AMERICA = 'North America'
    UNITED_STATES = 'United States'  # bonds: dollar bonds
    EUROPE = 'Europe'
    EURO_AREA = 'Euro Area'
    SWITZERLAND = 'Switzerland'
    UK = 'UK'
    JAPAN = 'Japan'
    ASIA_PACIFIC = 'Asia Pacific'
    EMERGING_MARKETS = 'Emerging Markets'
    OTHER = 'Other'


# Degiro's regions (of equity funds) and the region they belong to
DEGIRO_REGIONS = {
    'Worldwide': Region.GLOBAL,
    'North America': Region.NORTH_AMERICA,
    'United States': Region.NORTH_AMERICA,
    'Canada': Region.NORTH_AMERICA,
    'Europe': Region.EUROPE,
    'Scandinavia': Region.EUROPE,
    'Norway': Region.EUROPE,
    'Euro Area': Region.EURO_AREA,
    'Germany': Region.EURO_AREA,
    'France': Region.EURO_AREA,
    'Italy': Region.EURO_AREA,
    'Spain': Region.EURO_AREA,
    'Netherlands': Region.EURO_AREA,
    'Austria': Region.EURO_AREA,
    'Portugal': Region.EURO_AREA,
    'Switzerland': Region.SWITZERLAND,
    'Great Britain': Region.UK,
    'Japan': Region.JAPAN,
    'Asia Pacific': Region.ASIA_PACIFIC,
    'Asia Pacific ex Japan': Region.ASIA_PACIFIC,
    'Australia': Region.ASIA_PACIFIC,
    'Singapore': Region.ASIA_PACIFIC,
    'Emerging markets': Region.EMERGING_MARKETS,
    'China & Hong-Kong': Region.EMERGING_MARKETS,
    'Greater China': Region.EMERGING_MARKETS,
    'India': Region.EMERGING_MARKETS,
    'Korea': Region.EMERGING_MARKETS,
    'Taiwan': Region.EMERGING_MARKETS,
    'Brasil': Region.EMERGING_MARKETS,
    'Latin America': Region.EMERGING_MARKETS,
    'Mexico': Region.EMERGING_MARKETS,
    'Central & East Europe': Region.EMERGING_MARKETS,
    'Russia': Region.EMERGING_MARKETS,
    'Turkey': Region.EMERGING_MARKETS,
    'Indonesia': Region.EMERGING_MARKETS,
    'Malaysia': Region.EMERGING_MARKETS,
    'Thailand': Region.EMERGING_MARKETS,
    'Viet Nam': Region.EMERGING_MARKETS,
    'Africa': Region.EMERGING_MARKETS,
    'South Africa': Region.EMERGING_MARKETS,
    'Arabic world': Region.EMERGING_MARKETS,
    'Bangladesh': Region.EMERGING_MARKETS,
    'Pakistan': Region.EMERGING_MARKETS,
}

# the region named in a text, in this order (the more specific first; "ex ..." exclusions are removed beforehand)
REGION_RULES = [
    (
        Region.EMERGING_MARKETS,
        words(
            'emerging',
            'em',
            'frontier',
            'china',
            'chinese',
            'india',
            'indian',
            'brazil',
            'korea',
            'taiwan',
            'latin america',
            'latam',
            'eastern europe',
            'turkey',
            'poland',
            'indonesia',
            'vietnam',
            'saudi',
            'mexico',
            'south africa',
            'csi 300',
            'msci em',
        ),
    ),
    (
        Region.EURO_AREA,
        words(
            'emu',
            'euro ?stoxx',
            'eurozone',
            'euro area',
            'euro',
            'germany',
            'german',
            'dax',
            'mdax',
            'tecdax',
            'sdax',
            'france',
            'french',
            'cac',
            'italy',
            'italian',
            'mib',
            'spain',
            'spanish',
            'ibex',
            'netherlands',
            'aex',
            'austria',
            'atx',
            'belgium',
            'portugal',
            'finland',
            'bund',
            'bunds',
            'btp',
            'oat',
            'eb\\.rexx',
        ),
    ),
    (Region.SWITZERLAND, words('switzerland', 'swiss', 'smi', 'smim', 'spi', 'sbi', 'sli')),
    (Region.UK, words('united kingdom', 'uk', 'ftse 100', 'ftse 250', 'britain', 'british', 'gilts?', 'sterling')),
    (Region.JAPAN, words('japan', 'japanese', 'nikkei', 'topix', 'jpx')),
    (Region.ASIA_PACIFIC, words('asia', 'asian', 'pacific', 'australia', 'hong kong', 'singapore', 'far east')),
    (
        Region.NORTH_AMERICA,
        words(
            'usa',
            'u\\.s\\.',
            'us',
            'united states',
            'america',
            'american',
            's&p 500',
            's&p500',
            'nasdaq',
            'russell',
            'canada',
            'canadian',
            'tsx',
            'dow jones industrial',
        ),
    ),
    (Region.EUROPE, words('europe', 'european', 'stoxx', 'nordic', 'scandinavia', 'sweden', 'norway', 'denmark')),
    (
        Region.GLOBAL,
        words('world', 'global', 'acwi', 'all[- ]world', 'all country', 'international', 'intl', 'developed'),
    ),
]
EXCLUSION = re.compile(r'\bex[- ](?:\w+[- ])?\w+', re.IGNORECASE)  # e.g. "ex Japan", "ex-US", "ex EMU"

# the market of the bonds held, from their currency next to a bond word (e.g. "USD Treasury", "Euro Corporate")
BOND_WORD = (
    r'(?:bonds?|bd|treasury|treasuries|govt|government|corp|corporate|high yield|aggregate|aggrt|inflation|'
    r'tips|ultrashort|float|credit|sovereign|covered|liquid|t-bills?|overnight)'
)
BOND_MARKETS = [
    (Region.EURO_AREA, re.compile(r'\b(?:eur|euro)\b(?:\W+\w+){0,3}?\W+' + BOND_WORD, re.IGNORECASE)),
    (Region.UNITED_STATES, re.compile(r'(?:\busd\b|\$|\bus\b|\bu\.s\.)(?:\W+\w+){0,3}?\W+' + BOND_WORD, re.IGNORECASE)),
    (Region.SWITZERLAND, re.compile(r'\bchf\b(?:\W+\w+){0,3}?\W+' + BOND_WORD, re.IGNORECASE)),
    (Region.UK, re.compile(r'\b(?:gbp|sterling)\b(?:\W+\w+){0,3}?\W+' + BOND_WORD, re.IGNORECASE)),
]
# currency hedged share classes, e.g. "CHF Hedged", "(hedged to EUR)", "HEUR", "USDH", "(CHFh)", "Hdg"
CURRENCIES = r'(eur|usd|chf|gbp|jpy)'
HEDGED = [
    re.compile(r'\b' + CURRENCIES + r'[- ]?(?:hedged|hdg|hgd)\b', re.IGNORECASE),
    re.compile(r'\b(?:hedged|hdg|hgd)\W+(?:to\W+)?' + CURRENCIES + r'\b', re.IGNORECASE),
    re.compile(r'\bh' + CURRENCIES + r'\b|\b' + CURRENCIES + r'h\b', re.IGNORECASE),
]
HEDGED_WORD = words('hedged', 'hdg', 'hgd')

SEGMENT_RULES = [  # equity segments, in this order
    (
        'Thematic',
        words(
            'clean energy',
            'renewable',
            'solar',
            'wind',
            'hydrogen',
            'batter(?:y|ies)',
            'electric vehicles?',
            'ev',
            'robotics',
            'automation',
            'artificial intelligence',
            'ai',
            'cyber ?security',
            'cloud',
            'blockchain',
            'metaverse',
            'gaming',
            'esports',
            'defen[cs]e',
            'space',
            'water',
            'lithium',
            'uranium',
            'nuclear',
            'miners?',
            'mining',
            'genomics',
            'fintech',
            'digital',
            'innovation',
            'disruptive',
            'millennials',
            'ageing',
            'aging',
            'megatrends?',
            'cannabis',
            'semiconductors?',
            'chips',
            'internet',
            'e-commerce',
            'biotech(?:nology)?',
            'future',
            'smart',
            'circular',
            'climate',
            'carbon',
            'agri(?:business)?',
            'food',
            'infrastructure',
            'transition',
            'mobility',
            'healthcare innovation',
            'medical',
        ),
    ),
    (
        'Sector',
        words(
            'banks?',
            'banking',
            'financials?',
            'insurance',
            'energy',
            'oil & gas',
            'oil and gas',
            'utilities',
            'health ?care',
            'pharma(?:ceuticals)?',
            'technology',
            'tech',
            'information technology',
            'industrials?',
            'materials',
            'basic resources',
            'chemicals',
            'consumer',
            'staples',
            'discretionary',
            'telecom(?:s|munications)?',
            'communication(?:s)?',
            'media',
            'retail',
            'automobiles?',
            'autos?',
            'construction',
            'travel',
            'leisure',
            'personal & household',
            'food & beverage',
        ),
    ),
    (
        'Factor',
        words(
            'dividends?',
            'value',
            'growth',
            'momentum',
            'quality',
            'min(?:imum)? vol(?:atility)?',
            'low vol(?:atility)?',
            'small[- ]?caps?',
            'mid[- ]?caps?',
            'equal weight',
            'multi[- ]?factor',
            'factor',
            'high yield',
            'buyback',
            'aristocrats',
            'income',
            'covered call',
            'put write',
            'enhanced',
            'risk weighted',
        ),
    ),
]
BOND_TYPE_RULES = [
    ('Inflation-linked', words('inflation', 'tips', 'linkers?', 'linked')),
    ('High Yield', words('high yield', 'hy', 'fallen angels', 'crossover', 'junk')),
    (
        'Short-term',
        words(
            't-bills?',
            'treasury bills?',
            'ultra ?short',
            '0-1',
            '0-3 months?',
            '1-3 months?',
            '3-month',
            'short term',
            'float(?:ing)? rate',
            'float t',  # abbreviated "Float Rate Treasury" (not "Float Adjusted" indices)
            'money',
        ),
    ),
    ('Corporate', words('corp', 'corporates?', 'credit', 'investment grade', 'ig', 'covered')),
    (
        'Government',
        words(
            'govt',
            'government',
            'treasury',
            'treasuries',
            'sovereigns?',
            'bunds?',
            'btp',
            'oat',
            'gilts?',
            'states?',
            'eb\\.rexx',
        ),
    ),
    ('Aggregate', words('aggregate', 'aggrt', 'aggr', 'core', 'universal', 'total')),
]
COMMODITY_RULES = [
    ('Precious metals', words('gold', 'silver', 'platinum', 'palladium', 'precious', 'rhodium', 'platin')),
    (
        'Energy',
        words(
            'oil',
            'brent',
            'wti',
            'crude',
            'natural gas',
            'gas',
            'heating',
            'gasoline',
            'henry hub',
            'carbon',
            'eua',
            'energy',
        ),
    ),
    ('Industrial metals', words('copper', 'nickel', 'aluminium', 'aluminum', 'zinc', 'lead', 'tin', 'industrial')),
    (
        'Agriculture',
        words(
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
            'grains?',
            'agri(?:culture)?',
        ),
    ),
]
CRYPTO_BASKET = words(
    'basket', 'index', 'altcoins?', 'mega ?cap', 'equal weight', 'coindesk', 'market', 'top', 'diversified', 'crypto'
)
MONEY_MARKETS = [
    (Region.EURO_AREA, words('eur', 'euro', '€str', 'estr', 'eonia')),
    (Region.UNITED_STATES, words('usd', 'sofr', 'fed funds', 'us')),
    (Region.SWITZERLAND, words('chf', 'saron')),
    (Region.UK, words('gbp', 'sonia', 'sterling')),
]


class SubClass(NamedTuple):
    """Sub-class of an ETF: sub_class (segment, bond type, commodity or crypto group; None if not applicable), region
    (None if not applicable or unknown), region source (degiro, benchmark, name), hedged and hedge currency.
    """

    sub_class: str | None
    region: str | None
    region_source: str | None
    hedged: bool
    hedge_currency: str | None


def first(rules: list[tuple[str, re.Pattern]], *texts: str) -> tuple[str, int] | None:
    """The first rule matching one of the texts (tried in order), with the index of the text."""
    for n, text in enumerate(texts):
        for value, pattern in rules:
            if text and pattern.search(text):
                return value, n
    return None


def hedge(name: str) -> tuple[bool, str | None]:
    """Whether a share class is currency hedged, and to which currency (None if not named)."""
    for pattern in HEDGED:
        match = pattern.search(name)
        if match:
            return True, next(g for g in match.groups() if g).upper()
    return bool(HEDGED_WORD.search(name)), None


def unhedged(text: str) -> str:
    """The text without its hedging (the hedge currency is not the market of the fund)."""
    for pattern in HEDGED:
        text = pattern.sub(' ', text)
    return HEDGED_WORD.sub(' ', text)


def sub_classify(asset_class: str, name: str | None, benchmark: str | None, degiro_region: str | None) -> SubClass:
    """Sub-class, region and hedging of an ETF of a macro asset class, from its name, its benchmark and its Degiro
    region (None if unknown).
    """
    name, benchmark = name or '', benchmark or ''
    hedged, hedge_currency = hedge(name)
    texts = (unhedged(benchmark), unhedged(name))
    sources = ('benchmark', 'name')
    stripped = tuple(EXCLUSION.sub(' ', t) for t in texts)  # "World ex USA" is not North America

    def region(rules=REGION_RULES) -> tuple[str | None, str | None]:
        found = first(rules, *stripped)
        return (found[0], sources[found[1]]) if found else (None, None)

    if asset_class == 'Equity':
        if degiro_region in DEGIRO_REGIONS:
            region_found = DEGIRO_REGIONS[degiro_region], 'degiro'
        else:
            region_found = region()
        segment = first(SEGMENT_RULES, *texts)
        return SubClass(segment[0] if segment else 'Broad', *region_found, hedged, hedge_currency)
    if asset_class == 'Fixed Income':
        market = first(BOND_MARKETS, *texts)
        if market:
            region_found = market[0], sources[market[1]]
        else:
            region_found = region()
            if region_found[0] == Region.NORTH_AMERICA:
                region_found = Region.UNITED_STATES, region_found[1]
        bond_type = first(BOND_TYPE_RULES, *texts)
        return SubClass(bond_type[0] if bond_type else 'Other', *region_found, hedged, hedge_currency)
    if asset_class == 'Real Estate':
        return SubClass(None, *region(), hedged, hedge_currency)
    if asset_class == 'Money Market':
        return SubClass(None, *region(MONEY_MARKETS), hedged, hedge_currency)
    if asset_class == 'Commodities':
        group = first(COMMODITY_RULES, *texts)
        return SubClass(group[0] if group else 'Broad', None, None, hedged, hedge_currency)
    if asset_class == 'Crypto':
        text = f'{name} {benchmark}'
        bitcoin = bool(words('bitcoin', 'btc').search(text))
        ether = bool(words('ethereum', 'ether', 'eth').search(text))
        if CRYPTO_BASKET.search(text) and not (bitcoin or ether) or (bitcoin and ether):
            group = 'Basket'
        elif bitcoin:
            group = 'Bitcoin'
        elif ether:
            group = 'Ethereum'
        else:
            group = 'Other coin'
        return SubClass(group, None, None, hedged, hedge_currency)
    return SubClass(None, None, None, hedged, hedge_currency)
