import numpy as np
import pandas as pd
import sys

from definitions import Accounts
from portfolio_analysis_funcs import vol_risk_contr
from portfolio_history import load_hist_portfolio_data, update_data
from portfolio_performance import load_portfolio_performance, load_benchmark_performance
from portfolio_performance import compute_portfolio_performance
from products import load_portfolio_products


class PortfolioData:
    def __init__(self, account: Accounts):
        self.account = account
        self.hist_data = load_hist_portfolio_data(account=self.account)
        self.perf_dct = load_portfolio_performance(account=self.account)
        self.perf_bm_dct = load_benchmark_performance(account=self.account)
        self.prod_df = load_portfolio_products(account=self.account)
        self.prod_df['symbol'] = self.prod_df['symbol'].fillna(self.prod_df['isin'])
        self.prod_df['name'] = self.prod_df['name'].fillna(self.prod_df['isin'])
        self.prod_df.loc[self.prod_df.duplicated('symbol', keep=False), 'symbol'] = self.prod_df.loc[
            self.prod_df.duplicated('symbol', keep=False), ['symbol', 'currency']].agg('_'.join, axis=1)
        self.returns_adj_weekly_df = self.hist_data.close_adj.resample('W-WED').last().pct_change()
        self.alloc_risk_df = self.get_alloc_risk()

    def update(self):
        update_data(account=self.account)
        compute_portfolio_performance(account=self.account)
        self.__init__(account=self.account)

    def get_alloc_risk(self) -> pd.DataFrame:
        weights_last = self.hist_data.effective_weights.T.iloc[:, -1].rename('Allocation')
        risk_contrib = vol_risk_contr(w=self.hist_data.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
                                      cov_mat=self.returns_adj_weekly_df.cov().values)
        risk_contrib = pd.DataFrame(np.append(risk_contrib, 0.),
                                    columns=['Risk contrib.'],
                                    index=self.hist_data.effective_weights.columns)
        alloc_risk_df = pd.concat([weights_last, risk_contrib, self.prod_df[['name', 'symbol', 'isin']]], axis=1)
        alloc_risk_df = alloc_risk_df.sort_values(by='Allocation', ascending=False)
        row_cash = alloc_risk_df.iloc[alloc_risk_df.index == 'Cash', :].fillna('Cash')
        alloc_risk_df = alloc_risk_df.drop('Cash', axis=0)
        alloc_risk_df = alloc_risk_df[alloc_risk_df['Allocation'] > sys.float_info.epsilon]
        return pd.concat([alloc_risk_df, row_cash])
