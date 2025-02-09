from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

import file_utils as fu
from date_utils import reset_time
from charts import fetch_portfolio_charts, load_portfolio_charts, fetch_fx_charts, load_fx_rates
from definitions import DATA_DIR, PORTFOLIO_NAME, BASE_CURRENCY
from portfolio import Portfolio, HistPortfolioData
from products import fetch_portfolio_products, load_portfolio_products
from transactions import fetch_account_movements, load_account_movements
from transactions import fetch_tx_history, load_tx_history, TxHistFields
from instruments_performance import fetch_portfolio_instr_adj_prices


def compute_hist_nav(prices_df: pd.DataFrame,
                     tx_hist_df: pd.DataFrame,
                     initial_cash_balance: float = 1e4,
                     div_hist_df: Optional[pd.DataFrame] = None,
                     fx_rates_df: Optional[pd.DataFrame] = None,
                     dep_hist_df: Optional[pd.DataFrame] = None,
                     close_adj_df: Optional[pd.DataFrame] = None,
                     ) -> HistPortfolioData:
    # initialize
    units = np.zeros_like(prices_df)
    effective_weights = np.zeros_like(prices_df)
    nav = np.zeros(len(prices_df))
    cash_balance = np.zeros(len(prices_df))
    transaction_value = np.zeros(len(prices_df))
    transaction_costs = np.zeros(len(prices_df))
    dividends = np.zeros_like(prices_df)
    deposits = np.zeros(len(prices_df))

    # build initial portfolio
    portfolio = Portfolio(prices_df=prices_df, initial_cash_balance=initial_cash_balance)

    # loop over t
    for t in np.arange(0, len(prices_df)):
        current_prices = prices_df.iloc[t, :]

        # rebalance
        if prices_df.index[t] in tx_hist_df['Date'].to_list():
            portfolio.rebalance(tx_history_df=tx_hist_df.loc[tx_hist_df['Date'] == prices_df.index[t]])
            transaction_value[t] = portfolio.transaction_value
            transaction_costs[t] = portfolio.transaction_costs

        # add dividends
        if div_hist_df is not None:
            if prices_df.index[t] in div_hist_df['Date'].to_list():
                div_hist_t = div_hist_df.loc[div_hist_df['Date'] == prices_df.index[t], :]
                portfolio.add_cash(div_hist_t['amount_base_currency'].sum())
                for n in range(len(div_hist_t)):
                    idx = prices_df.columns.to_list().index(div_hist_t.iloc[n, :][TxHistFields.symbol])
                    dividends[t, idx] = div_hist_t.iloc[n, :]['amount_base_currency']

        # add deposits and subtract withdrawals
        if dep_hist_df is not None:
            if prices_df.index[t] in dep_hist_df['Date'].to_list():
                deposit = dep_hist_df.loc[dep_hist_df['Date'] == prices_df.index[t], 'change'].sum()
                portfolio.add_cash(deposit)
                deposits[t] = deposit

        # store
        units[t, :] = portfolio.current_units
        cash_balance[t] = portfolio.current_cash_balance
        effective_weights[t, :] = portfolio.get_effective_weights(current_prices=current_prices)
        nav[t] = portfolio.get_nav(current_prices=current_prices)

    units_df = pd.DataFrame(units, columns=prices_df.columns, index=prices_df.index)
    effective_weights_df = pd.DataFrame(effective_weights, columns=prices_df.columns, index=prices_df.index)
    effective_weights_df['Cash'] = cash_balance / nav
    nav = pd.Series(nav, name='NAV', index=prices_df.index)
    transaction_value = pd.Series(transaction_value, name='Tx value', index=prices_df.index)
    transaction_costs = pd.Series(transaction_costs, name='Tx costs', index=prices_df.index)
    dividends = pd.DataFrame(dividends, columns=prices_df.columns, index=prices_df.index)
    div_yield = dividends.div(units * prices_df).resample('Y').sum()
    deposits = pd.Series(deposits, name='Deposits', index=prices_df.index)
    returns = (nav - deposits).div(nav.shift(1)).sub(1.).fillna(0.)
    nav_eff = 100. * returns.add(1.).cumprod().rename('NAV Effective')
    cum_pnl = prices_df.diff().mul(units_df.shift(1)).add(dividends).cumsum()
    units_df['Cash'] = cash_balance

    hist_portfolio_data = HistPortfolioData(nav=nav,
                                            cum_pnl=cum_pnl,
                                            div_yield=div_yield,
                                            units=units_df,
                                            target_weights=None,
                                            effective_weights=effective_weights_df,
                                            transaction_costs=transaction_costs,
                                            transaction_value=transaction_value,
                                            prices=prices_df,
                                            dividends=dividends,
                                            fx_rates=fx_rates_df,
                                            deposits=deposits,
                                            nav_eff=nav_eff,
                                            close_adj=close_adj_df)
    return hist_portfolio_data


def save_hist_portfolio_data(hist_portfolio_data: HistPortfolioData):
    fu.save_df_dict_to_excel(df_dict=hist_portfolio_data._asdict(),
                             file_name=PORTFOLIO_NAME,
                             folder=DATA_DIR)


def load_hist_portfolio_data() -> HistPortfolioData:
    data_dict = fu.load_df_dict_from_excel(file_name=PORTFOLIO_NAME, folder=DATA_DIR)
    return HistPortfolioData(**data_dict)


def update_data():
    fetch_tx_history()
    fetch_portfolio_products()
    fetch_account_movements()
    fetch_portfolio_charts()
    fetch_fx_charts()


def compute_hist_portfolio_data() -> HistPortfolioData:
    # prices
    prices_df = load_portfolio_charts()
    # transaction history
    tx_hist_df = load_tx_history()
    # dividends
    account_mvmts_df = load_account_movements()
    dividends_df = account_mvmts_df.loc[account_mvmts_df['description'].isin(['Dividendo', 'Cedola']), :]
    # deposits/withdrawals
    deposits_df = account_mvmts_df.loc[account_mvmts_df['description'].isin(['Deposito', 'Prelievo',
                                                                             'Deposito flatex',
                                                                             'Prelievo flatex']), :]
    # forex rates
    products_df = load_portfolio_products()
    curr_foreign_lst = list(set(products_df['currency'].to_list() + dividends_df['currency'].to_list()))
    curr_foreign_lst = [c for c in curr_foreign_lst if c != BASE_CURRENCY]
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst, index=prices_df.index)

    product_ids = list(set(tx_hist_df[TxHistFields.product_id].to_list()))
    product_symbols = products_df.loc[product_ids, 'symbol'].fillna(products_df.loc[product_ids, 'id']).to_list()
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
    ts_start = tx_hist_df['Date'].iloc[0]
    prices_df = prices_df.loc[prices_df.index >= ts_start, product_symbols].ffill()
    fx_rates_df = fx_rates_df.loc[fx_rates_df.index >= ts_start, :].reindex(prices_df.index).ffill()
    deposits_df = deposits_df.loc[deposits_df.index >= ts_start, :]

    # check all dividend dates are in the price datetime index
    assert all([d in prices_df.index for d in dividends_df['Date']])
    assert all([d in prices_df.index for d in deposits_df['Date']])

    # currency conversion to base currency
    for symbol, cur in zip(product_symbols, product_curr):
        prices_df.loc[:, symbol] = prices_df[symbol].mul(fx_rates_df.loc[prices_df.index, f'{cur}/{BASE_CURRENCY}'])
    fx_rates = [fx_rates_df.loc[dividends_df.iloc[n]['Date'], f'{cur}/{BASE_CURRENCY}']
                for n, cur in enumerate(dividends_df['currency'])]
    dividends_df.loc[:, 'fx_rate'] = fx_rates
    dividends_df.loc[:, 'amount_base_currency'] = dividends_df['change'].mul(dividends_df['fx_rate'])

    # adjusted closing prices from Yahoo Finance
    close_adj_df = fetch_portfolio_instr_adj_prices()

    # compute historical portfolio data
    hist_portfolio_data = compute_hist_nav(prices_df=prices_df,
                                           tx_hist_df=tx_hist_df,
                                           initial_cash_balance=initial_cash_balance,
                                           div_hist_df=dividends_df,
                                           fx_rates_df=fx_rates_df,
                                           dep_hist_df=deposits_df,
                                           close_adj_df=close_adj_df)
    save_hist_portfolio_data(hist_portfolio_data)
    return hist_portfolio_data


class UnitTests(Enum):
    UPDATE_DATA = 1
    COMPUTE_NAV = 2


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.UPDATE_DATA:
        update_data()
    elif unit_test == UnitTests.COMPUTE_NAV:
        compute_hist_portfolio_data()


if __name__ == '__main__':
    unit_test = UnitTests.UPDATE_DATA
    run_unit_test(unit_test=unit_test)
