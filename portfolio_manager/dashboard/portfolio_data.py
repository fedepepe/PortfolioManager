"""Data of the Portfolio page."""

import sys

import numpy as np
import pandas as pd

from portfolio_manager.analytics.metrics import compute_portfolio_metrics
from portfolio_manager.analytics.performance import load_performance_data_benchmark, load_performance_data_portfolio
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.backtest.workflows import load_backtest_data
from portfolio_manager.config.accounts import Account
from portfolio_manager.config.settings import DEFAULT_CORR_DATA_FREQ
from portfolio_manager.degiro.products import adjust_prod_column_labels, load_portfolio_products
from portfolio_manager.optimization.objectives import vol_risk_contr
from portfolio_manager.storage.models import Product


class AllocationRiskLabels:
    """Labels of the allocation and risk contribution table."""

    ALLOCATION = 'Allocation'
    RISK_CONTRIB = 'Risk contrib.'
    NAME = Product.name.name
    SYMBOL = Product.symbol.name
    ISIN = Product.isin.name


# tables of the portfolio backtest shown by the Portfolio page (the others are not loaded)
PORTFOLIO_PAGE_FIELDS = ['nav_eff', 'effective_weights', 'close_adj', 'prices']


# read-only view of the saved data of a portfolio, shared through dashboard.data_service: do not modify
class PortfolioData:
    """Backtest, performance and products of an account, as shown on the Portfolio page."""

    def __init__(self, account: Account | None = None, hist_data: PortfolioBacktestData | None = None):
        if account is not None:
            self.account = account
            self.hist_data = load_backtest_data(name=self.account.name, fields=PORTFOLIO_PAGE_FIELDS)
            self.perf_dct = load_performance_data_portfolio(account=self.account)
            self.perf_bm_dct = load_performance_data_benchmark(account=self.account)
            self.prod_df = adjust_prod_column_labels(load_portfolio_products(account=self.account))
        elif hist_data is not None:
            self.account = None
            self.hist_data = hist_data
            self.perf_dct = compute_portfolio_metrics(nav=hist_data.nav_eff)
            self.perf_bm_dct = None
            self.prod_df = adjust_prod_column_labels(hist_data.prices)
        else:
            raise ValueError
        self.returns_adj_weekly_df = self.hist_data.close_adj.resample(DEFAULT_CORR_DATA_FREQ).last().pct_change()
        self.alloc_risk_df = self.get_alloc_risk()

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
