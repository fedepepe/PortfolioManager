"""Data of the Portfolio page."""

import sys

import numpy as np
import pandas as pd

from portfolio_manager.analytics.performance import load_performance_data_benchmark, load_performance_data_portfolio
from portfolio_manager.backtest.workflows import load_backtest_data
from portfolio_manager.config.accounts import Account
from portfolio_manager.config.settings import DEFAULT_CORR_DATA_FREQ
from portfolio_manager.degiro.products import adjust_prod_column_labels, load_portfolio_products
from portfolio_manager.optimization.objectives import vol_risk_contr
from portfolio_manager.storage.models import Product
from portfolio_manager.storage.queries import query_etf_classes


class AllocationRiskLabels:
    """Labels of the allocation and risk contribution table."""

    ALLOCATION = 'Allocation'
    RISK_CONTRIB = 'Risk contrib.'
    NAME = Product.name.name
    SYMBOL = Product.symbol.name
    ISIN = Product.isin.name


# tables of the portfolio backtest shown by the Portfolio page (the others are not loaded)
PORTFOLIO_PAGE_FIELDS = ['cum_time_weighted_return', 'effective_weights', 'close_adj']

UNKNOWN = 'Unknown'  # the class or region of an instrument that is not known
CASH = 'Cash'
# asset class of the products that are not classified ETFs, by Degiro product type
PRODUCT_TYPE_CLASSES = {'STOCK': 'Equity', 'BOND': 'Fixed Income'}
# region of a bond or stock held directly, from the country of its ISIN (the issuer's): the regions of the ETF
# classification (analytics.subclasses); the United States are North America for equity
EURO_AREA_COUNTRIES = set('AT BE CY DE EE ES FI FR GR HR IE IT LT LU LV MT NL PT SI SK'.split())
ISIN_COUNTRY_REGIONS = {'CH': 'Switzerland', 'GB': 'UK', 'JP': 'Japan', 'US': 'United States'}


def isin_region(isin: str | None, asset_class: str) -> str:
    """Region of a bond or stock held directly, from the country of its ISIN (Unknown for other countries)."""
    country = isin[:2] if isinstance(isin, str) else ''
    if country in EURO_AREA_COUNTRIES:
        return 'Euro Area'
    region = ISIN_COUNTRY_REGIONS.get(country, UNKNOWN)
    return 'North America' if region == 'United States' and asset_class == 'Equity' else region


def classify_holdings(holdings: pd.DataFrame, product_types: pd.Series) -> pd.DataFrame:
    """Asset class and region of each instrument held (holdings: weight, symbol, isin by product): a classified ETF
    has those of the ETF classification; a bond or stock held directly gets its class from its product type and its
    region from its ISIN; anything else is Unknown.
    """
    classes = query_etf_classes(holdings['isin'].dropna().to_list())
    rows = []
    for product, row in holdings.iterrows():
        isin = row['isin']
        if isinstance(isin, str) and isin in classes.index:
            asset_class = classes.at[isin, 'asset_class'] or UNKNOWN
            region = classes.at[isin, 'region'] or UNKNOWN
        else:
            asset_class = PRODUCT_TYPE_CLASSES.get(product_types.get(product), UNKNOWN)
            region = isin_region(isin, asset_class) if asset_class != UNKNOWN else UNKNOWN
        rows.append((product, row['weight'], row['symbol'], asset_class, region))
    return pd.DataFrame(rows, columns=['product', 'weight', 'symbol', 'asset_class', 'region']).set_index('product')


def breakdown(holdings: pd.DataFrame, column: str, cash: float = 0.0) -> pd.DataFrame:
    """Weights by asset class or region (column) with the symbols of each: the largest first, then the cash if given
    (otherwise the weights are those of the invested part), Unknown last.
    """
    grouped = holdings.groupby(column).agg(weight=('weight', 'sum'), symbols=('symbol', lambda s: ', '.join(s)))
    known = grouped.drop(UNKNOWN, errors='ignore').sort_values('weight', ascending=False)
    parts = [known]
    if cash > 0:
        parts.append(pd.DataFrame({'weight': [cash], 'symbols': [CASH]}, index=[CASH]))
    if UNKNOWN in grouped.index:
        parts.append(grouped.loc[[UNKNOWN]])
    table = pd.concat(parts)
    if cash <= 0:
        table['weight'] = table['weight'] / table['weight'].sum()
    return table


# read-only view of the saved data of a portfolio, shared through dashboard.data_service: do not modify
class PortfolioData:
    """Backtest, performance and products of an account, as shown on the Portfolio page."""

    def __init__(self, account: Account):
        self.account = account
        self.hist_data = load_backtest_data(name=self.account.name, fields=PORTFOLIO_PAGE_FIELDS)
        self.perf_dct = load_performance_data_portfolio(account=self.account)
        self.perf_bm_dct = load_performance_data_benchmark(account=self.account)
        self.prod_df = adjust_prod_column_labels(load_portfolio_products(account=self.account))
        self.returns_adj_weekly_df = self.hist_data.close_adj.resample(DEFAULT_CORR_DATA_FREQ).last().pct_change()
        self.alloc_risk_df = self.get_alloc_risk()
        held = self.alloc_risk_df.drop(CASH, errors='ignore')
        holdings = pd.DataFrame(
            {
                'weight': held[AllocationRiskLabels.ALLOCATION].astype(float),
                'symbol': held[AllocationRiskLabels.SYMBOL],
                'isin': held[AllocationRiskLabels.ISIN],
            }
        )
        self.holdings_df = classify_holdings(holdings, self.prod_df[Product.product_type.name])
        cash = self.alloc_risk_df[AllocationRiskLabels.ALLOCATION].get(CASH, 0.0)
        self.asset_classes_df = breakdown(self.holdings_df, 'asset_class', cash=float(cash))
        self.geography_df = breakdown(self.holdings_df, 'region')

    def get_alloc_risk(self) -> pd.DataFrame:
        """Latest weights and contributions to the portfolio risk of each instrument."""
        weights_last = self.hist_data.effective_weights.T.iloc[:, -1].rename(AllocationRiskLabels.ALLOCATION)
        risk_contrib = vol_risk_contr(
            w=self.hist_data.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
            cov_mat=self.returns_adj_weekly_df.cov().values,
        )
        risk_contrib = pd.DataFrame(
            np.append(risk_contrib, 0.0),
            columns=[AllocationRiskLabels.RISK_CONTRIB],
            index=self.hist_data.effective_weights.columns,
        )
        alloc_risk_df = pd.concat(
            [weights_last, risk_contrib, self.prod_df[[Product.name.name, Product.symbol.name, Product.isin.name]]],
            axis=1,
        )
        alloc_risk_df = alloc_risk_df.sort_values(by=AllocationRiskLabels.ALLOCATION, ascending=False)
        row_cash = alloc_risk_df.iloc[alloc_risk_df.index == 'Cash', :].fillna('Cash')
        alloc_risk_df = alloc_risk_df.drop('Cash', axis=0)
        alloc_risk_df = alloc_risk_df[alloc_risk_df[AllocationRiskLabels.ALLOCATION] > sys.float_info.epsilon]
        return pd.concat([alloc_risk_df, row_cash])
