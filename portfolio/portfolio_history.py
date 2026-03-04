from enum import Enum
from typing import Optional, List

import numpy as np
import pandas as pd

from utils import file_utils as fu
from degiro.charts import fetch_portfolio_charts, load_portfolio_charts, fetch_fx_charts, load_fx_rates
from utils.date_utils import reset_time
from definitions import Accounts, PortfolioAllocationStrats
from definitions import DATA_DIR
from definitions import DEFAULT_DATA_FREQ
from degiro.degiro_connection import get_degiro_connection
from portfolio.instruments_performance import fetch_portfolio_instr_adj_prices, fetch_instr_adj_prices
from portfolio.portfolio_generic import Portfolio, HistPortfolioData
from degiro.product_definitions import Currencies
from degiro.products import fetch_portfolio_products_info, load_portfolio_products
from degiro.transactions import fetch_account_movements, load_account_movements
from degiro.transactions import fetch_tx_history, load_tx_history, TxHistFields
from engines.portfolio_optimization import compute_weights_optim_portfolio


def compute_hist_portfolio_data(name: str,
                                prices_df: pd.DataFrame,
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
                                ) -> HistPortfolioData:
    # initialize
    units = np.zeros_like(prices_df)
    effective_weights = np.zeros_like(prices_df)
    nav = np.zeros(len(prices_df))
    cash_balance = np.zeros(len(prices_df))
    txn_value = np.zeros_like(prices_df)
    txn_costs = np.zeros_like(prices_df)
    dividends = np.zeros_like(prices_df)
    deposits = np.zeros(len(prices_df))

    if freq_rebalancing is None:
        rebalancing_dates = prices_df.index
    else:
        rebalancing_dates = prices_df.resample(freq_rebalancing).last().index

    # build initial portfolio
    portfolio = Portfolio(tickers=prices_df.columns.to_list(),
                          base_currency=curr_base,
                          initial_cash_balance=initial_cash_balance)

    # loop over t
    for t in np.arange(0, len(prices_df)):
        current_prices = prices_df.iloc[t, :]

        # rebalance
        if target_exp is not None:
            if isinstance(target_exp, pd.DataFrame):
                if prices_df.index[t] in target_exp.index:
                    portfolio.rebalance(target_exp=target_exp.iloc[t, :], current_prices=current_prices)
            elif isinstance(target_exp, List):
                if t == 0 or prices_df.index[t] in rebalancing_dates:
                    portfolio.rebalance(target_exp=np.array(target_exp), current_prices=current_prices)
        elif target_units is not None:
            if prices_df.index[t] in target_units.index:
                portfolio.rebalance(units=target_units.iloc[t, :], current_prices=current_prices)
        elif tx_hist_df is not None:
            if prices_df.index[t] in tx_hist_df['Date'].to_list():
                portfolio.rebalance(tx_hist_df=tx_hist_df.loc[tx_hist_df['Date'] == prices_df.index[t]])
                txn_value[t, :] = portfolio.txn_value
                txn_costs[t, :] = portfolio.txn_costs

        # add dividends
        if div_hist_df is not None:
            if prices_df.index[t] in div_hist_df['Date'].to_list():
                div_hist_t = div_hist_df.loc[div_hist_df['Date'] == prices_df.index[t], :]
                portfolio.add_cash(div_hist_t['amount_base_currency'].sum())
                for n in range(len(div_hist_t)):
                    idx = prices_df.columns.to_list().index(div_hist_t.iloc[n, :][TxHistFields.product_id])
                    dividends[t, idx] = div_hist_t.iloc[n, :]['amount_base_currency']

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
    txn_value = pd.DataFrame(txn_value, columns=prices_df.columns, index=prices_df.index)
    txn_costs = pd.DataFrame(txn_costs, columns=prices_df.columns, index=prices_df.index)
    dividends = pd.DataFrame(dividends, columns=prices_df.columns, index=prices_df.index)
    div_yield = dividends.div(units * prices_df).resample('Y').sum()
    deposits = pd.Series(deposits, name='Deposits', index=prices_df.index)
    returns = (nav - deposits).div(nav.shift(1)).sub(1.).fillna(0.)
    nav_eff = 100. * returns.add(1.).cumprod().rename('NAV Effective')
    cum_pnl = prices_df.mul(units).diff().add(dividends).add(txn_value).add(txn_costs).cumsum()
    units['Cash'] = cash_balance

    if close_adj_df is not None:
        missing_tickers = [t for t in prices_df if t not in close_adj_df]
        close_adj_df[missing_tickers] = prices_df[missing_tickers]
        close_adj_df = close_adj_df[prices_df.columns]

    hist_portfolio_data = HistPortfolioData(name=name,
                                            nav=nav,
                                            cum_pnl=cum_pnl,
                                            div_yield=div_yield,
                                            units=units,
                                            target_weights=None,
                                            effective_weights=effective_weights_df,
                                            transaction_costs=txn_costs,
                                            transaction_value=txn_value,
                                            prices=prices_df,
                                            dividends=dividends.resample('M').sum(),
                                            fx_rates=fx_rates_df,
                                            deposits=deposits,
                                            nav_eff=nav_eff,
                                            close_adj=close_adj_df)
    return hist_portfolio_data


def save_hist_portfolio_data(hist_portfolio_data: HistPortfolioData):
    fu.save_df_dict_to_excel(df_dict=hist_portfolio_data._asdict(),
                             file_name=hist_portfolio_data.name,
                             folder_name=DATA_DIR)


def load_hist_portfolio_data(account: Accounts) -> HistPortfolioData:
    data_dict = fu.load_df_dict_from_excel(file_name=account.name, folder_name=DATA_DIR)
    data_dict['name'] = account.name
    return HistPortfolioData(**data_dict)


def update_data(account: Accounts):
    conn = get_degiro_connection(file_name=account.config_file)
    fetch_tx_history(account=account, degiro_conn=conn)
    fetch_portfolio_products_info(account=account, degiro_conn=conn)
    fetch_account_movements(account=account, degiro_conn=conn)
    fetch_portfolio_charts(account=account, degiro_conn=conn)
    fetch_fx_charts(account=account, degiro_conn=conn)


def compute_hist_portfolio_data_account(account: Accounts) -> HistPortfolioData:
    # prices
    prices_df = load_portfolio_charts(account=account)
    # transaction history
    tx_hist_df = load_tx_history(account=account)
    # dividends
    account_mvmts_df = load_account_movements(account=account)
    dividends_df = account_mvmts_df.loc[account_mvmts_df['description'].isin(['Dividendo',
                                                                              'Cedola',
                                                                              'Imposta sulla Cedola']), :].copy()
    # deposits/withdrawals
    deposits_df = account_mvmts_df.loc[account_mvmts_df['description'].isin(['Deposito',
                                                                             'Prelievo',
                                                                             'Deposito flatex',
                                                                             'Prelievo flatex']), :].copy()
    # forex rates
    products_df = load_portfolio_products(account=account)
    curr_foreign_lst = list(set(products_df['currency'].to_list() + dividends_df['currency'].to_list()))
    curr_foreign_lst = [c for c in curr_foreign_lst if c != account.currency]
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst,
                                account=account,
                                index=prices_df.index)

    product_ids = list(set(tx_hist_df[TxHistFields.product_id].to_list()))
    product_curr = products_df.loc[product_ids, 'currency'].to_list()

    # compute initial cash balance
    initial_cash_balance = deposits_df.loc[deposits_df.index <= tx_hist_df.index[0], 'change'].sum()

    # reset datetime and restrict to a suitable timeframe
    tx_hist_df[TxHistFields.symbol] = products_df.loc[tx_hist_df[TxHistFields.product_id], 'symbol'].to_list()
    tx_hist_df[TxHistFields.symbol] = tx_hist_df[TxHistFields.symbol].fillna(tx_hist_df.product_id)
    tx_hist_df.loc[:, 'Date'] = [reset_time(ts) for ts in tx_hist_df.index]
    dividends_df[TxHistFields.symbol] = products_df.loc[dividends_df[TxHistFields.product_id], 'symbol'].to_list()
    dividends_df[TxHistFields.symbol] = dividends_df[TxHistFields.symbol].fillna(dividends_df.product_id)
    dividends_df.loc[:, 'Date'] = [reset_time(ts) for ts in dividends_df['value_date']]
    deposits_df.loc[:, 'Date'] = [reset_time(ts) for ts in deposits_df['value_date']]
    ts_start = tx_hist_df['Date'].iloc[0] - pd.tseries.offsets.BDay(1)
    prices_df = prices_df.loc[prices_df.index >= ts_start, product_ids].ffill()
    fx_rates_df = fx_rates_df.loc[fx_rates_df.index >= ts_start, :].reindex(prices_df.index).ffill()
    deposits_df = deposits_df.loc[deposits_df.index >= ts_start, :]

    # check all dividend dates are in the price datetime index
    assert all([d in prices_df.index for d in dividends_df['Date']])
    assert all([d in prices_df.index for d in deposits_df['Date']])

    # currency conversion to base currency
    for prod_id, curr in zip(product_ids, product_curr):
        prices_df.loc[:, prod_id] = prices_df[prod_id].mul(fx_rates_df.loc[prices_df.index, f'{curr}/{account.currency}'])
    fx_rates = [fx_rates_df.loc[dividends_df.iloc[n]['Date'], f'{curr}/{account.currency}']
                for n, curr in enumerate(dividends_df['currency'])]
    dividends_df.loc[:, 'fx_rate'] = fx_rates
    dividends_df.loc[:, 'amount_base_currency'] = dividends_df['change'].mul(dividends_df['fx_rate'])

    # adjusted closing prices from Yahoo Finance
    close_adj_df = fetch_portfolio_instr_adj_prices(account=account)

    # compute historical portfolio data
    hist_portfolio_data = compute_hist_portfolio_data(name=account.name,
                                                      prices_df=prices_df,
                                                      tx_hist_df=tx_hist_df,
                                                      curr_base=account.currency,
                                                      initial_cash_balance=initial_cash_balance,
                                                      div_hist_df=dividends_df,
                                                      fx_rates_df=fx_rates_df,
                                                      dep_hist_df=deposits_df,
                                                      close_adj_df=close_adj_df)
    save_hist_portfolio_data(hist_portfolio_data=hist_portfolio_data)
    return hist_portfolio_data


def compute_hist_portfolio_data_benchmark(account: Accounts,
                                          index: pd.DatetimeIndex) -> HistPortfolioData:
    prices_adj_df = fetch_instr_adj_prices(account=account,
                                           isin_lst=[v[2] for v in account.benchmark.values()],
                                           tick_lst=list(account.benchmark.keys()))
    hist_benchmark_data = compute_hist_portfolio_data(name=f'{account.name}_benchmark',
                                                      prices_df=prices_adj_df.reindex(index=index).ffill(),
                                                      target_exp=[v[0] for v in account.benchmark.values()],
                                                      curr_base=account.currency,
                                                      freq_rebalancing=[v[1] for v in account.benchmark.values()][0])
    save_hist_portfolio_data(hist_portfolio_data=hist_benchmark_data)
    return hist_benchmark_data


def compute_hist_portfolio_data_optimized(account: Accounts,
                                          index: pd.DatetimeIndex) -> HistPortfolioData:
    prices_adj_df = fetch_portfolio_instr_adj_prices(account=account)
    target_exp_df = compute_weights_optim_portfolio(allocation_method=PortfolioAllocationStrats.MAX_SHARPE,
                                                    prices=prices_adj_df,
                                                    sampling_freq=DEFAULT_DATA_FREQ,
                                                    optimization_freq='M')
    hist_optimized_data = compute_hist_portfolio_data(name=f'{account.name}_optimized',
                                                      prices_df=prices_adj_df.reindex(index=index).ffill(),
                                                      target_exp=target_exp_df,
                                                      curr_base=account.currency)
    return hist_optimized_data


class UnitTests(Enum):
    UPDATE_DATA = 1
    COMPUTE_HIST_PORTFOLIO_ACCOUNT = 2


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.UPDATE_DATA:
        for account in Accounts:
            update_data(account=account)
    elif unit_test == UnitTests.COMPUTE_HIST_PORTFOLIO_ACCOUNT:
        for account in Accounts:
            compute_hist_portfolio_data_account(account=account)
    else:
        raise NotImplementedError


if __name__ == '__main__':
    unit_test = UnitTests.COMPUTE_HIST_PORTFOLIO_ACCOUNT
    run_unit_test(unit_test=unit_test)
