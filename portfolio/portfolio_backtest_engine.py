from typing import Optional, List, Dict

import numpy as np
import pandas as pd

from config.accounts import Accounts, Brokers
from degiro.portfolio_backtest import PortfolioDegiro
from degiro.transactions import TxHistFields
from portfolio.portfolio import Portfolio, PortfolioBacktestData, Currencies


def backtest_portfolio(prices_df: pd.DataFrame,
                       name: Optional[str] = None,
                       account: Optional[Accounts] = None,
                       target_exp: Optional[pd.DataFrame | List] = None,
                       target_units: Optional[pd.DataFrame] = None,
                       tx_hist_df: Optional[pd.DataFrame] = None,
                       curr_base: Currencies = Currencies.USD,
                       initial_cash_balance: float = 1e6,
                       div_hist_df: Optional[pd.DataFrame] = None,
                       fx_rates_df: Optional[pd.DataFrame] = None,
                       dep_hist_df: Optional[pd.DataFrame] = None,
                       close_adj_df: Optional[pd.DataFrame] = None,
                       freq_rebalancing: Optional[str] = None,
                       id_symbol_map: Optional[Dict] = None
                       ) -> PortfolioBacktestData:
    if name is None and account is None:
        raise AttributeError
    if account is not None:
        name = account.name

    # initialize
    units = np.zeros_like(prices_df)
    effective_weights = np.zeros_like(prices_df)
    nav = np.zeros(len(prices_df))
    cash_balance = np.zeros(len(prices_df))
    txn_values = np.zeros_like(prices_df)
    txn_costs = np.zeros_like(prices_df)
    dividends = np.zeros_like(prices_df)
    deposits = np.zeros(len(prices_df))

    if freq_rebalancing is None:
        rebalancing_dates = prices_df.index
    else:
        rebalancing_dates = prices_df.resample(freq_rebalancing).last().index

    # build initial portfolio
    if account is not None:
        if account.broker == Brokers.DEGIRO:
            portfolio = PortfolioDegiro(tickers=prices_df.columns.to_list(),
                                        base_currency=account.currency,
                                        initial_cash_balance=initial_cash_balance)
        else:
            raise NotImplementedError
    else:
        portfolio = Portfolio(tickers=prices_df.columns.to_list(),
                              base_currency=curr_base,
                              initial_cash_balance=initial_cash_balance,
                              txn_costs_prop_bp=10,
                              max_target_dev=0.01,
                              min_cash_amount=0.05 * initial_cash_balance)

    # loop over t
    for t in np.arange(0, len(prices_df)):
        current_prices = prices_df.iloc[t, :]

        # rebalance
        if target_exp is not None:
            if isinstance(target_exp, pd.DataFrame):
                if prices_df.index[t] in target_exp.index:
                    portfolio.rebalance(target_exp=target_exp.loc[prices_df.index[t], :], current_prices=current_prices)
            elif isinstance(target_exp, List):
                if t == 0 or prices_df.index[t] in rebalancing_dates:
                    portfolio.rebalance(target_exp=np.array(target_exp), current_prices=current_prices)
        elif target_units is not None:
            if prices_df.index[t] in target_units.index:
                portfolio.rebalance(units=target_units.loc[prices_df.index[t], :], current_prices=current_prices)
        elif tx_hist_df is not None:
            if prices_df.index[t] in tx_hist_df['Date'].to_list():
                portfolio.rebalance(tx_hist_df=tx_hist_df.loc[tx_hist_df['Date'] == prices_df.index[t]])
                txn_values[t, :] = portfolio.txn_values
                txn_costs[t, :] = portfolio.txn_costs

        # add dividends
        if div_hist_df is not None:
            if prices_df.index[t] in div_hist_df['Date'].to_list():
                div_hist_t = div_hist_df.loc[div_hist_df['Date'] == prices_df.index[t], :]
                portfolio.add_cash(div_hist_t['amount_base_currency'].sum())
                for n in range(len(div_hist_t)):
                    idx = prices_df.columns.to_list().index(div_hist_t.iloc[n, :][TxHistFields.product_id])
                    dividends[t, idx] += div_hist_t.iloc[n, :]['amount_base_currency']

        # add deposits and subtract withdrawals
        if dep_hist_df is not None:
            if prices_df.index[t] in dep_hist_df['Date'].to_list():
                deposit = dep_hist_df.loc[dep_hist_df['Date'] == prices_df.index[t], 'change'].sum()
                portfolio.add_cash(deposit)
                deposits[t] = deposit

        # store
        units[t, :] = portfolio.current_units
        cash_balance[t] = portfolio.get_current_cash_balance()
        effective_weights[t, :] = portfolio.get_effective_weights(current_prices=current_prices)
        nav[t] = portfolio.get_nav(current_prices=current_prices)

    units = pd.DataFrame(units, columns=prices_df.columns, index=prices_df.index)
    effective_weights_df = pd.DataFrame(effective_weights, columns=prices_df.columns, index=prices_df.index)
    effective_weights_df['Cash'] = cash_balance / nav
    nav = pd.Series(nav, name='NAV', index=prices_df.index)
    txn_values = pd.DataFrame(txn_values, columns=prices_df.columns, index=prices_df.index)
    txn_costs = pd.DataFrame(txn_costs, columns=prices_df.columns, index=prices_df.index)
    dividends = pd.DataFrame(dividends, columns=prices_df.columns, index=prices_df.index)
    div_yield = dividends.div(units.replace(0, np.nan).ffill() * prices_df).replace(np.inf, np.nan).resample('Y').sum()
    deposits = pd.Series(deposits, name='Deposits', index=prices_df.index)
    returns = (nav - deposits).div(nav.shift(1)).sub(1.).fillna(0.)
    nav_eff = 100. * returns.add(1.).cumprod().rename('NAV Effective')
    cum_pnl = prices_df.mul(units).diff().add(dividends).add(txn_values).add(txn_costs).cumsum()
    units['Cash'] = cash_balance

    if close_adj_df is not None:
        missing_tickers = [t for t in prices_df if t not in close_adj_df]
        close_adj_df[missing_tickers] = prices_df[missing_tickers]
        close_adj_df = close_adj_df[prices_df.columns]

    hist_portfolio_data = PortfolioBacktestData(name=name,
                                                nav=nav,
                                                cum_pnl=cum_pnl,
                                                div_yield=div_yield,
                                                units=units,
                                                target_weights=None,
                                                effective_weights=effective_weights_df,
                                                transaction_costs=txn_costs,
                                                transaction_value=txn_values,
                                                prices=prices_df,
                                                dividends=dividends.resample('M').sum(),
                                                fx_rates=fx_rates_df,
                                                deposits=deposits,
                                                nav_eff=nav_eff,
                                                close_adj=close_adj_df,
                                                id_symbol_map=id_symbol_map)
    return hist_portfolio_data
