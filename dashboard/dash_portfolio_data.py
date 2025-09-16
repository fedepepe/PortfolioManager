import numpy as np
import pandas as pd

from definitions import Accounts
from portfolio import HistPortfolioData
from portfolio_analysis_funcs import vol_risk_contr
from portfolio_history import load_hist_portfolio_data, update_data
from portfolio_history import compute_hist_portfolio_data, compute_hist_benchmark_data
from portfolio_performance import load_portfolio_performance, compute_portfolio_performance
from products import load_portfolio_products


class PortfolioData:
    def __init__(self, account: Accounts):
        self.account = account
        self.hist_data = load_hist_portfolio_data(account=self.account)
        self.perf_dct = load_portfolio_performance(account=self.account)
        self.prod_df = load_portfolio_products(account=self.account)
        self.returns_adj_weekly_df = self.hist_data.close_adj.resample('W-WED').last().pct_change()
        self.alloc_risk_df = self.get_alloc_risk()

    def update(self):
        update_data(account=self.account)
        compute_hist_portfolio_data(account=self.account)
        compute_portfolio_performance(account=self.account)
        self.__init__(account=self.account)

    def get_alloc_risk(self) -> pd.DataFrame:
        weights_last = self.hist_data.effective_weights.T.iloc[:, -1].rename('Allocation')
        risk_contrib = vol_risk_contr(w=self.hist_data.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
                                      cov_mat=self.returns_adj_weekly_df.cov().values)
        risk_contrib = pd.DataFrame(np.append(risk_contrib, 0.),
                                    columns=['Risk contrib.'],
                                    index=self.hist_data.effective_weights.columns)
        prod_df = self.prod_df.copy()
        prod_df.loc[:, 'symbol'] = prod_df['symbol'].fillna(self.prod_df['id'])
        prod_df = prod_df.set_index('symbol')
        alloc_risk_df = pd.concat([weights_last, risk_contrib, prod_df['name']], axis=1)
        alloc_risk_df = alloc_risk_df.sort_values(by='Allocation', ascending=False)
        alloc_risk_df['name'] = alloc_risk_df['name'].fillna(alloc_risk_df.index.to_series())
        row_cash = alloc_risk_df.iloc[alloc_risk_df.index == 'Cash', :]
        alloc_risk_df = alloc_risk_df.drop('Cash', axis=0)
        return pd.concat([alloc_risk_df, row_cash])


def get_portfolio_data(account: Accounts) -> PortfolioData:
    if not hasattr(get_portfolio_data, 'portfolio_data'):
            get_portfolio_data.portfolio_data = PortfolioData(account=account)
    return get_portfolio_data.portfolio_data


def get_benchmark_data(account: Accounts, index: pd.DatetimeIndex) -> HistPortfolioData:
    if not hasattr(get_benchmark_data, 'portfolio_data'):
            get_benchmark_data.portfolio_data = compute_hist_benchmark_data(account=account, index=index)
    return get_benchmark_data.portfolio_data
