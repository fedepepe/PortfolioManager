"""Data update and backtests of the accounts, their benchmarks and optimized portfolios."""

import os
import re
import time
from dataclasses import asdict

import pandas as pd

from portfolio_manager.analytics.instruments import fetch_instr_adj_prices
from portfolio_manager.analytics.metrics import compute_results_from_navs
from portfolio_manager.analytics.performance import compute_portfolio_performance, save_performance_data
from portfolio_manager.backtest.adjusted_prices import get_portfolio_adj_prices
from portfolio_manager.backtest.engine import backtest_portfolio
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.config.accounts import Accounts
from portfolio_manager.config.settings import DATA_DIR, DEFAULT_DATA_FREQ
from portfolio_manager.degiro.charts import (
    fetch_fx_charts,
    fetch_portfolio_charts,
    load_fx_rates,
    load_portfolio_charts,
)
from portfolio_manager.degiro.connection import get_degiro_connection
from portfolio_manager.degiro.products import (
    adjust_prod_column_labels,
    fetch_portfolio_products_info,
    load_portfolio_products,
)
from portfolio_manager.degiro.transactions import (
    TxHistFields,
    fetch_account_movements,
    fetch_tx_history,
    load_account_movements,
    load_tx_history,
)
from portfolio_manager.optimization.optimizer import OptimizedWeights, compute_weights_optim_portfolio
from portfolio_manager.optimization.settings import OptimizationSettings
from portfolio_manager.storage import files as fu
from portfolio_manager.storage.tables import (
    derived_folder,
    has_tables,
    load_attributes,
    load_tables,
    save_tables,
)
from portfolio_manager.utils.dates import reset_time

BACKTESTS = 'backtests'  # kind of derived result (folder of the Parquet tables)


def _backtest_tables(hist_portfolio_data: PortfolioBacktestData) -> dict[str, pd.DataFrame | pd.Series]:
    return {k: v for k, v in asdict(hist_portfolio_data).items() if isinstance(v, pd.DataFrame | pd.Series)}


def save_backtest_data(hist_portfolio_data: PortfolioBacktestData):
    """Save a backtest: Parquet tables (working copy) and an Excel copy for inspection, with symbols instead of
    product ids as column labels when the backtest has an id-symbol map (file name ending in _visual).
    """
    save_backtest_tables(hist_portfolio_data)
    tables = _backtest_tables(hist_portfolio_data)
    id_symbol_map = hist_portfolio_data.id_symbol_map
    if id_symbol_map is not None:
        tables = {k: v.rename(columns=id_symbol_map) if isinstance(v, pd.DataFrame) else v for k, v in tables.items()}
    file_name = f'{hist_portfolio_data.name}_visual' if id_symbol_map is not None else hist_portfolio_data.name
    fu.save_df_dict_to_excel(df_dict=tables, file_name=file_name, folder_name=DATA_DIR)


def save_backtest_tables(hist_portfolio_data: PortfolioBacktestData, saved_at: float | None = None):
    """Save the Parquet tables of a backtest; saved_at (default: now) is the time the backtest was computed."""
    id_symbol_map = hist_portfolio_data.id_symbol_map
    attributes = {
        'freq': hist_portfolio_data.freq,
        'id_symbol_map': [[k, v] for k, v in id_symbol_map.items()] if id_symbol_map is not None else None,
        'saved_at': saved_at if saved_at is not None else time.time(),
    }
    folder = derived_folder(BACKTESTS, hist_portfolio_data.name)
    save_tables(folder, _backtest_tables(hist_portfolio_data), attributes=attributes)


def load_backtest_data(name: str, fields: list[str] | None = None) -> PortfolioBacktestData:
    """Saved backtest with the given name: all its tables, or only the given fields (the others are None)."""
    folder = derived_folder(BACKTESTS, name)
    if not has_tables(folder):
        return load_backtest_data_excel(name, fields=fields)  # saved before the Parquet tables
    attributes = load_attributes(folder)
    id_symbol_map = attributes.get('id_symbol_map')
    return PortfolioBacktestData(
        name=name,
        freq=attributes.get('freq'),
        id_symbol_map=dict(id_symbol_map) if id_symbol_map is not None else None,
        **load_tables(folder, names=fields),
    )


def backtest_saved_at(name: str) -> float | None:
    """Time the saved backtest was computed, None if there is none."""
    folder = derived_folder(BACKTESTS, name)
    if has_tables(folder):
        return load_attributes(folder)['saved_at']
    excel_file = os.path.join(DATA_DIR, f'{fu.to_file_name(name)}.xlsx')
    return os.path.getmtime(excel_file) if os.path.isfile(excel_file) else None


BACKTEST_SERIES_FIELDS = ['nav', 'nav_eff', 'deposits']


def load_backtest_data_excel(name: str, fields: list[str] | None = None) -> PortfolioBacktestData:
    """Backtest saved as an Excel file (before the Parquet tables)."""
    data_dict = fu.load_df_dict_from_excel(file_name=name, folder_name=DATA_DIR)
    if fields is not None:
        data_dict = {k: v for k, v in data_dict.items() if k in fields}
    # series are saved as one-column sheets: restore them as series
    for field in BACKTEST_SERIES_FIELDS:
        if isinstance(data_dict.get(field), pd.DataFrame) and data_dict[field].shape[1] == 1:
            data_dict[field] = data_dict[field].iloc[:, 0]
    data_dict['name'] = name
    return PortfolioBacktestData(**data_dict)


def update_data(account: Accounts):
    """Download the transactions, products, cash movements and charts of an account from Degiro."""
    conn = get_degiro_connection(account=account)
    fetch_tx_history(account=account, degiro_conn=conn)
    fetch_portfolio_products_info(account=account, degiro_conn=conn)
    fetch_account_movements(account=account, degiro_conn=conn)
    fetch_portfolio_charts(account=account, degiro_conn=conn)
    fetch_fx_charts(account=account, degiro_conn=conn)


# descriptions of the Degiro cash movements (in the language of the account: Italian)
DIVIDEND_PATTERN = 'Dividendo|Cedola'  # dividends, coupons (and their taxes)
FX_PATTERN = 'Credito FX|Prelievo FX'  # currency conversions of the cash account
DEPOSIT_PATTERN = 'Deposito|Prelievo'  # deposits and withdrawals
SPLIT_PATTERN = 'FRAZIONAMENTO'  # split adjustment: one movement out (old units), one in (new units)
PRODUCT_CHANGE_PATTERN = 'CAMBIO'  # product change: a sale of the old product and a purchase of the new one


def backtest_portfolio_account(account: Accounts) -> PortfolioBacktestData:
    """Backtest of the account from its saved Degiro data (trades, cash movements, prices), saved to its file."""
    prices_df = load_portfolio_charts(account=account)
    tx_hist_df = load_tx_history(account=account)
    movements_df = load_account_movements(account=account)
    dividends_df = movements_df.loc[movements_df['description'].str.contains(DIVIDEND_PATTERN), :].copy()
    movements_df = movements_df.loc[~movements_df['description'].str.contains(FX_PATTERN), :]
    deposits_df = movements_df.loc[movements_df['description'].str.contains(DEPOSIT_PATTERN), :].copy()

    products_df = adjust_prod_column_labels(load_portfolio_products(account=account))
    products_df['symbol'] = products_df['symbol'].fillna(products_df['name'])
    curr_foreign_lst = sorted(set(products_df['currency'].to_list() + dividends_df['currency'].to_list()))
    curr_foreign_lst = [c for c in curr_foreign_lst if c != account.currency]
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst, account=account, index=prices_df.index)

    product_ids = list(set(tx_hist_df[TxHistFields.product_id].to_list()))
    tx_hist_df = tx_hist_df.dropna(subset=['order_type_id'])
    initial_cash_balance = deposits_df.loc[deposits_df.index <= tx_hist_df.index[0], 'change'].sum()

    # trades and dividends of a product replaced by another one are attributed to the new product, then the
    # trades before a split are converted into post-split units
    for id_old, id_new in product_changes(movements_df).items():
        tx_hist_df[TxHistFields.product_id] = tx_hist_df[TxHistFields.product_id].replace(id_old, id_new)
        dividends_df[TxHistFields.product_id] = dividends_df[TxHistFields.product_id].replace(id_old, id_new)
    adjust_tx_for_splits(tx_hist_df, split_multipliers(movements_df))

    # dates without time, from the day before the first trade
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
    assert all(d in prices_df.index for d in dividends_df['Date'])
    assert all(d in prices_df.index for d in deposits_df['Date'])

    product_curr = products_df.loc[product_ids, 'currency'].to_list()
    prices_df = _prices_to_base_currency(prices_df, product_ids, product_curr, fx_rates_df, account.currency)
    dividends_df = _dividends_to_base_currency(dividends_df, fx_rates_df, account.currency)

    backtest_data = backtest_portfolio(
        account=account,
        prices_df=prices_df,
        tx_hist_df=tx_hist_df,
        curr_base=account.currency,
        initial_cash_balance=initial_cash_balance,
        div_hist_df=dividends_df,
        fx_rates_df=fx_rates_df,
        dep_hist_df=deposits_df,
        # adjusted closing prices from Yahoo Finance (or the database when offline), labelled by product id
        close_adj_df=get_portfolio_adj_prices(account=account).prices,
        id_symbol_map=dict(zip(product_ids, products_df.loc[product_ids, 'symbol'].to_list(), strict=True)),
    )
    save_backtest_data(hist_portfolio_data=backtest_data)
    return backtest_data


def split_multipliers(movements_df: pd.DataFrame) -> pd.DataFrame:
    """Unit multiplier of each split, by date (index) and product: e.g. 36 units becoming 900 gives 25."""
    splits_df = movements_df[movements_df['description'].str.contains(SPLIT_PATTERN)].copy()
    splits_df['mult'] = [int(re.search(r'\d+', s).group()) for s in splits_df['description']]
    # the units leaving the account (negative change) multiply, the units entering divide
    splits_df['mult'] = splits_df['mult'].where(splits_df['change'] < 0, 1.0 / splits_df['mult'])
    return splits_df.groupby([splits_df.index.name, 'product_id'])['mult'].prod().reset_index(level=1)


def adjust_tx_for_splits(tx_hist_df: pd.DataFrame, splits_df: pd.DataFrame):
    """Converts the trades before each split into post-split units and prices (in place)."""
    for ts, prod_id, mult in zip(splits_df.index, splits_df['product_id'], splits_df['mult'], strict=True):
        mask = (tx_hist_df.index <= ts) & (tx_hist_df['product_id'] == prod_id)
        tx_hist_df.loc[mask, TxHistFields.quantity] = tx_hist_df.loc[mask, TxHistFields.quantity].mul(mult)
        tx_hist_df.loc[mask, TxHistFields.price] = tx_hist_df.loc[mask, TxHistFields.price].div(mult)


def product_changes(movements_df: pd.DataFrame) -> dict:
    """Old product id -> new product id, from the sale and purchase movements of each product change."""
    changes_df = movements_df[movements_df['description'].str.contains(PRODUCT_CHANGE_PATTERN)].copy()
    changes_df = changes_df.set_index('value_date')
    changes_df['product'] = [re.search(r'\d+(.*?)@', s).group(1) for s in changes_df['description']]
    changes = {}
    for _, change in changes_df.groupby([changes_df.index.name, 'product']):
        id_old = change.loc[change['description'].str.contains('Vendita'), 'product_id'].iloc[0]
        id_new = change.loc[change['description'].str.contains('Acquisto'), 'product_id'].iloc[0]
        changes[id_old] = id_new
    return changes


def _prices_to_base_currency(
    prices_df: pd.DataFrame, product_ids: list, product_curr: list, fx_rates_df: pd.DataFrame, base_currency: str
) -> pd.DataFrame:
    for prod_id, curr in zip(product_ids, product_curr, strict=True):
        prices_df.loc[:, prod_id] = prices_df[prod_id].mul(fx_rates_df.loc[prices_df.index, f'{curr}/{base_currency}'])
    return prices_df


def _dividends_to_base_currency(
    dividends_df: pd.DataFrame, fx_rates_df: pd.DataFrame, base_currency: str
) -> pd.DataFrame:
    dividends_df.loc[:, 'fx_rate'] = [
        fx_rates_df.loc[date, f'{curr}/{base_currency}']
        for date, curr in zip(dividends_df['Date'], dividends_df['currency'], strict=True)
    ]
    dividends_df.loc[:, 'amount_base_currency'] = dividends_df['change'].mul(dividends_df['fx_rate'])
    return dividends_df


def backtest_portfolio_benchmark(account: Accounts, index: pd.DatetimeIndex) -> PortfolioBacktestData:
    """Backtest of the benchmark of an account over the given dates (saved)."""
    prices_adj_df = fetch_instr_adj_prices(
        account=account, isin_lst=[v[2] for v in account.benchmark.values()], tick_lst=list(account.benchmark.keys())
    )
    backtest_data_benchmark = backtest_portfolio(
        name=f'{account.name}_benchmark',
        prices_df=prices_adj_df.reindex(index=index).ffill(),
        target_exp=[v[0] for v in account.benchmark.values()],
        curr_base=account.currency,
        freq_rebalancing=[v[1] for v in account.benchmark.values()][0],
    )
    save_backtest_data(hist_portfolio_data=backtest_data_benchmark)
    return backtest_data_benchmark


def refresh_account(account: Accounts):
    """Fetch new data from Degiro, then recompute and save backtest and performance of portfolio and benchmark."""
    update_data(account=account)
    hist_portfolio_data = backtest_portfolio_account(account=account)
    hist_benchmark_data = backtest_portfolio_benchmark(account=account, index=hist_portfolio_data.nav.index)
    compute_portfolio_performance(
        account=account, hist_portfolio_data=hist_portfolio_data, hist_benchmark_data=hist_benchmark_data
    )


OPTIMIZATION_SETTINGS_LABEL = 'settings'


def optimized_portfolio_name(account: Accounts) -> str:
    """Name of the saved backtest and performance files of the optimized portfolio."""
    return f'{account.name} Opt. (Tangency)'


def optimization_prices(account: Accounts) -> tuple[pd.DataFrame, str]:
    """Full history of adjusted prices (Yahoo Finance, or the database when offline) and where they come from; products
    without adjusted prices use the unadjusted Degiro prices of the saved backtest (portfolio period only). One
    column per instrument: products with the same ISIN (e.g. one ETF on two exchanges) have the same prices, so only
    one is kept, preferring the product held at the end of the backtest.
    """
    adj_prices = get_portfolio_adj_prices(account=account)
    hist_data = load_backtest_data(name=account.name)
    prices_df = adj_prices.prices.reindex(index=adj_prices.prices.index.union(hist_data.prices.index))
    for prod_id in hist_data.prices.columns:
        if prod_id not in prices_df.columns:
            prices_df[prod_id] = hist_data.prices[prod_id]
    isin = load_portfolio_products(account=account).set_index('id')['isin']
    units_last = hist_data.units.iloc[-1]
    keep, seen = [], set()
    for prod_id in sorted(prices_df.columns, key=lambda c: -units_last.get(c, 0.0)):
        key = isin.get(prod_id)
        key = key if isinstance(key, str) else prod_id  # products without ISIN are always kept
        if key not in seen:
            seen.add(key)
            keep.append(prod_id)
    return prices_df[[c for c in prices_df.columns if c in keep]], adj_prices.summary


def backtest_portfolio_optimized(
    account: Accounts,
    index: pd.DatetimeIndex,
    prices_adj_df: pd.DataFrame | None = None,
    settings: OptimizationSettings | None = None,
) -> tuple[PortfolioBacktestData, OptimizedWeights]:
    """Backtest of the optimized portfolio over the given dates with the given settings; saves the backtest and its
    performance with the settings.
    """
    settings = settings or OptimizationSettings()
    if prices_adj_df is None:
        prices_adj_df, _ = optimization_prices(account=account)
    # only the optimization dates within the backtest period are used (the earlier history is used for estimation)
    optimized_weights = compute_weights_optim_portfolio(
        allocation_method=settings.method,
        prices=prices_adj_df,
        sampling_freq=DEFAULT_DATA_FREQ,
        optimization_freq=settings.optimization_freq,
        extra_args=settings.extra_args(),
        start_date=index[0],
    )
    portfolio_name = optimized_portfolio_name(account)
    backtest_data_optimized = backtest_portfolio(
        name=portfolio_name,
        prices_df=prices_adj_df.reindex(index=index).ffill(),
        target_exp=optimized_weights.weights,
        curr_base=account.currency,
    )
    # performance results saved together with the settings they were computed with
    results_dict = compute_results_from_navs(navs=backtest_data_optimized.nav, save=False)
    results_dict[OPTIMIZATION_SETTINGS_LABEL] = settings.to_series().to_frame()
    save_performance_data(results_dict=results_dict, file_name=portfolio_name)
    save_backtest_data(hist_portfolio_data=backtest_data_optimized)
    return backtest_data_optimized, optimized_weights
