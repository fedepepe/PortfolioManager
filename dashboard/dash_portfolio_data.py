import sys
from typing import Optional

import numpy as np
import pandas as pd

from config.accounts import Accounts
from config.definitions import DEFAULT_CORR_DATA_FREQ
from database.table_definitions import Product
from degiro.products import load_portfolio_products, adjust_prod_column_labels
from engines.reporting import compute_portfolio_metrics
from engines.portfolio_optimization_obj_funcs import vol_risk_contr
from portfolio.portfolio import PortfolioBacktestData
from portfolio.portfolio_backtest import load_backtest_data, update_data
from portfolio.portfolio_backtest import backtest_portfolio_account, backtest_portfolio_benchmark
from portfolio.portfolio_performance import load_performance_data_portfolio, load_performance_data_benchmark
from portfolio.portfolio_performance import save_performance_data


class AllocationRiskLabels:
    ALLOCATION = 'Allocation'
    RISK_CONTRIB = 'Risk contrib.'
    NAME = Product.name.name
    SYMBOL = Product.symbol.name
    ISIN = Product.isin.name


# TODO: this class needs to be reviewed
class PortfolioData:
    def __init__(self,
                 account: Optional[Accounts] = None,
                 hist_data: Optional[PortfolioBacktestData] = None):
        if account is not None:
            self.account = account
            self.hist_data = load_backtest_data(account=self.account)
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

    def update(self):
        update_data(account=self.account)
        hist_portfolio_data = backtest_portfolio_account(account=self.account)
        hist_benchmark_data = backtest_portfolio_benchmark(account=self.account, index=hist_portfolio_data.nav.index)
        results_dict = compute_portfolio_metrics(hist_portfolio_data=hist_portfolio_data,
                                                 strategy_benchmark=hist_benchmark_data.nav)
        results_bm_dict = compute_portfolio_metrics(nav=hist_benchmark_data.nav_eff)
        save_performance_data(results_dict=results_dict, file_name=self.account.name)
        save_performance_data(results_dict=results_bm_dict, file_name=f'{self.account.name}_benchmark')
        self.__init__(account=self.account)

    def get_alloc_risk(self) -> pd.DataFrame:
        weights_last = self.hist_data.effective_weights.T.iloc[:, -1].rename(AllocationRiskLabels.ALLOCATION)
        risk_contrib = vol_risk_contr(w=self.hist_data.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
                                      cov_mat=self.returns_adj_weekly_df.cov().values)
        risk_contrib = pd.DataFrame(np.append(risk_contrib, 0.),
                                    columns=[AllocationRiskLabels.RISK_CONTRIB],
                                    index=self.hist_data.effective_weights.columns)
        alloc_risk_df = pd.concat([weights_last, risk_contrib, self.prod_df[[Product.name.name,
                                                                             Product.symbol.name,
                                                                             Product.isin.name]]], axis=1)
        alloc_risk_df = alloc_risk_df.sort_values(by=AllocationRiskLabels.ALLOCATION, ascending=False)
        row_cash = alloc_risk_df.iloc[alloc_risk_df.index == 'Cash', :].fillna('Cash')
        alloc_risk_df = alloc_risk_df.drop('Cash', axis=0)
        alloc_risk_df = alloc_risk_df[alloc_risk_df[AllocationRiskLabels.ALLOCATION] > sys.float_info.epsilon]
        return pd.concat([alloc_risk_df, row_cash])
