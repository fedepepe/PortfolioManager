import pandas as pd
import pytest

import portfolio_manager.storage.queries as sql
from portfolio_manager.dashboard.portfolio_data import UNKNOWN, breakdown, classify_holdings, isin_region


@pytest.mark.parametrize(
    'isin, asset_class, region',
    [
        ('IT0005363111', 'Fixed Income', 'Euro Area'),  # an Italian government bond
        ('DE0001102580', 'Fixed Income', 'Euro Area'),
        ('CH0012032048', 'Equity', 'Switzerland'),
        ('GB00B03MLX29', 'Equity', 'UK'),
        ('US0378331005', 'Equity', 'North America'),  # a US stock: North America, as in the ETF regions
        ('US912810TM09', 'Fixed Income', 'United States'),  # a US Treasury: the market of the bonds
        ('XS1234567890', 'Fixed Income', UNKNOWN),  # an international ISIN: no country
        (None, 'Equity', UNKNOWN),
    ],
)
def test_region_of_a_bond_or_stock_from_its_isin(isin, asset_class, region):
    assert isin_region(isin, asset_class) == region


def test_holdings_classified(temp_db):
    classes = pd.DataFrame(
        [
            ('IE00B8BVCK12', 'iShares MSCI World', 'Equity', 'Broad', 'Global'),
            ('IE00B1TXHL60', 'iShares Listed Private Equity', 'Equity', 'Broad', None),  # region unknown
        ],
        columns=['isin', 'name', 'asset_class', 'sub_class', 'region'],
    ).assign(leveraged=False, hedged=False, source='degiro', classified_at=pd.Timestamp.now())
    sql.save_etf_classes(classes)
    holdings = pd.DataFrame(
        {
            'weight': [0.4, 0.3, 0.1, 0.1],
            'symbol': ['IWDC', 'BTP', 'IPRV', 'FUND'],
            'isin': ['IE00B8BVCK12', 'IT0005363111', 'IE00B1TXHL60', 'LU0000000001'],
        },
        index=[1, 2, 3, 4],
    )
    types = pd.Series({1: 'ETF', 2: 'BOND', 3: 'ETF', 4: 'ETF'})  # FUND: an ETF that is not classified
    table = classify_holdings(holdings, types)
    assert table[['asset_class', 'region']].values.tolist() == [
        ['Equity', 'Global'],
        ['Fixed Income', 'Euro Area'],
        ['Equity', UNKNOWN],
        [UNKNOWN, UNKNOWN],
    ]


def test_breakdowns_largest_first_cash_then_unknown_last():
    holdings = pd.DataFrame(
        {
            'weight': [0.1, 0.5, 0.2, 0.05],
            'symbol': ['A', 'B', 'C', 'D'],
            'asset_class': [UNKNOWN, 'Equity', 'Fixed Income', 'Equity'],
            'region': ['Global', UNKNOWN, 'Euro Area', 'Global'],
        }
    )
    classes = breakdown(holdings, 'asset_class', cash=0.15)  # the whole portfolio, with the cash
    assert classes.index.tolist() == ['Equity', 'Fixed Income', 'Cash', UNKNOWN]
    assert classes['weight'].tolist() == pytest.approx([0.55, 0.2, 0.15, 0.1])
    assert classes.loc['Equity', 'symbols'] == 'B, D'
    regions = breakdown(holdings, 'region')  # the invested part only
    assert regions.index.tolist() == ['Euro Area', 'Global', UNKNOWN]
    assert regions['weight'].tolist() == pytest.approx([0.2 / 0.85, 0.15 / 0.85, 0.5 / 0.85])
