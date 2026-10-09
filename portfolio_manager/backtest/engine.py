"""Day-by-day backtest of a portfolio."""

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from portfolio_manager.backtest.degiro_portfolio import PortfolioDegiro
from portfolio_manager.backtest.portfolio import Currencies, Portfolio, PortfolioBacktestData, PortfolioGeneric
from portfolio_manager.config.accounts import Account, Brokers
from portfolio_manager.degiro.transactions import TxHistFields

logger = logging.getLogger(__name__)


def align_to_index(dates: pd.DatetimeIndex, index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Map each date to the last date in index on or before it (NaT if none)."""
    pos = index.searchsorted(dates, side='right') - 1
    return pd.DatetimeIndex([index[p] if p >= 0 else pd.NaT for p in pos])


def align_df_to_index(df: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Move the dates of df to the price index (last date on or before); keep the last per date."""
    df = df.copy()
    df.index = align_to_index(pd.DatetimeIndex(df.index), index)
    df = df[df.index.notna()]
    return df[~df.index.duplicated(keep='last')]


@dataclass
class _DailyRecords:
    """Values recorded for each day of the backtest (rows) and instrument (columns)."""

    units: np.ndarray
    effective_weights: np.ndarray
    txn_values: np.ndarray
    txn_costs: np.ndarray
    dividends: np.ndarray
    nav: np.ndarray
    cash_balance: np.ndarray
    deposits: np.ndarray

    @classmethod
    def empty(cls, prices_df: pd.DataFrame) -> '_DailyRecords':
        n_days = len(prices_df)
        return cls(
            units=np.zeros_like(prices_df),
            effective_weights=np.zeros_like(prices_df),
            txn_values=np.zeros_like(prices_df),
            txn_costs=np.zeros_like(prices_df),
            dividends=np.zeros_like(prices_df),
            nav=np.zeros(n_days),
            cash_balance=np.zeros(n_days),
            deposits=np.zeros(n_days),
        )


def backtest_portfolio(
    prices_df: pd.DataFrame,
    name: str | None = None,
    account: Account | None = None,
    target_exp: pd.DataFrame | list | None = None,
    target_units: pd.DataFrame | None = None,
    tx_hist_df: pd.DataFrame | None = None,
    curr_base: Currencies = Currencies.USD,
    initial_cash_balance: float = 1e6,
    div_hist_df: pd.DataFrame | None = None,
    fx_rates_df: pd.DataFrame | None = None,
    dep_hist_df: pd.DataFrame | None = None,
    close_adj_df: pd.DataFrame | None = None,
    freq_rebalancing: str | None = None,
    id_symbol_map: dict | None = None,
) -> PortfolioBacktestData:
    """Daily simulation of a portfolio driven by one of (in order of precedence): target weights (a frame by date, or
    fixed weights rebalanced every freq_rebalancing), target units by date, or the trades of an account.
    """
    if name is None and account is None:
        raise AttributeError
    if account is not None:
        name = account.name

    # align target dates to the price index (e.g. month-ends falling on weekends or holidays)
    if isinstance(target_exp, pd.DataFrame):
        target_exp = align_df_to_index(target_exp, prices_df.index)
    if target_units is not None:
        target_units = align_df_to_index(target_units, prices_df.index)

    portfolio = _build_portfolio(prices_df, account, curr_base, initial_cash_balance)
    rebalancing_dates = _rebalancing_dates(prices_df.index, freq_rebalancing)
    trade_dates = set(tx_hist_df['Date']) if tx_hist_df is not None else set()
    records = _DailyRecords.empty(prices_df)

    for t, date in enumerate(prices_df.index):
        current_prices = prices_df.iloc[t, :]
        rebalanced = _rebalance(
            portfolio,
            date,
            current_prices,
            target_exp,
            target_units,
            tx_hist_df,
            trade_dates,
            is_first_day=t == 0,
            rebalancing_dates=rebalancing_dates,
        )
        if rebalanced:
            records.txn_values[t, :] = portfolio.txn_values
            records.txn_costs[t, :] = portfolio.txn_costs
        if div_hist_df is not None:
            records.dividends[t, :] += _add_dividends(portfolio, date, div_hist_df, prices_df.columns.to_list())
        if dep_hist_df is not None:
            records.deposits[t] = _add_deposits(portfolio, date, dep_hist_df)
        records.units[t, :] = portfolio.current_units
        records.cash_balance[t] = portfolio.get_current_cash_balance()
        records.effective_weights[t, :] = portfolio.get_effective_weights(current_prices=current_prices)
        records.nav[t] = portfolio.get_nav(current_prices=current_prices)

    return _to_backtest_data(name, prices_df, records, fx_rates_df, close_adj_df, id_symbol_map)


def _build_portfolio(
    prices_df: pd.DataFrame, account: Account | None, curr_base: Currencies, initial_cash_balance: float
) -> PortfolioGeneric:
    """An account replays its trades; otherwise a simulated portfolio rebalanced to targets."""
    tickers = prices_df.columns.to_list()
    if account is not None:
        if account.broker != Brokers.DEGIRO:
            raise NotImplementedError
        return PortfolioDegiro(
            tickers=tickers, base_currency=account.currency, initial_cash_balance=initial_cash_balance
        )
    return Portfolio(
        tickers=tickers,
        base_currency=curr_base,
        initial_cash_balance=initial_cash_balance,
        txn_costs_prop_bp=10,
        max_target_dev=0.01,
        min_cash_amount=0.0,
        min_cash_ratio=0.001,  # 0.1% of the NAV kept in cash to pay the transaction costs
    )


def _rebalancing_dates(index: pd.DatetimeIndex, freq_rebalancing: str | None) -> pd.DatetimeIndex:
    """Every day, or the last available trading date of each period."""
    if freq_rebalancing is None:
        return index
    return pd.DatetimeIndex(index.to_series().resample(freq_rebalancing).last().dropna())


def _rebalance(
    portfolio: PortfolioGeneric,
    date: pd.Timestamp,
    current_prices: pd.Series,
    target_exp: pd.DataFrame | list | None,
    target_units: pd.DataFrame | None,
    tx_hist_df: pd.DataFrame | None,
    trade_dates: set,
    is_first_day: bool,
    rebalancing_dates: pd.DatetimeIndex,
) -> bool:
    """Rebalances the portfolio if the date calls for it; returns whether it did."""
    if target_exp is not None:
        if isinstance(target_exp, pd.DataFrame):
            if date not in target_exp.index:
                return False
            portfolio.rebalance(target_exp=target_exp.loc[date, :], current_prices=current_prices)
        elif isinstance(target_exp, list):
            if not (is_first_day or date in rebalancing_dates):
                return False
            portfolio.rebalance(target_exp=np.array(target_exp), current_prices=current_prices)
        else:
            return False
    elif target_units is not None:
        if date not in target_units.index:
            return False
        portfolio.rebalance(units=target_units.loc[date, :], current_prices=current_prices)
    elif tx_hist_df is not None:
        if date not in trade_dates:
            return False
        portfolio.rebalance(tx_hist_df=tx_hist_df.loc[tx_hist_df['Date'] == date])
    else:
        return False
    return True


def _add_dividends(
    portfolio: PortfolioGeneric, date: pd.Timestamp, div_hist_df: pd.DataFrame, tickers: list
) -> np.ndarray:
    """Credits the dividends paid on the date; returns them by instrument."""
    dividends = np.zeros(len(tickers))
    div_day = div_hist_df.loc[div_hist_df['Date'] == date, :]
    if div_day.empty:
        return dividends
    portfolio.add_cash(div_day['amount_base_currency'].sum())
    for product_id, amount in zip(div_day[TxHistFields.product_id], div_day['amount_base_currency'], strict=True):
        dividends[tickers.index(product_id)] += amount
    return dividends


def _add_deposits(portfolio: PortfolioGeneric, date: pd.Timestamp, dep_hist_df: pd.DataFrame) -> float:
    """Credits the deposits (negative: withdrawals) of the date; returns their total."""
    dep_day = dep_hist_df.loc[dep_hist_df['Date'] == date, 'change']
    if dep_day.empty:
        return 0.0
    deposit = dep_day.sum()
    portfolio.add_cash(deposit)
    return deposit


def _to_backtest_data(
    name: str,
    prices_df: pd.DataFrame,
    records: _DailyRecords,
    fx_rates_df: pd.DataFrame | None,
    close_adj_df: pd.DataFrame | None,
    id_symbol_map: dict | None,
) -> PortfolioBacktestData:
    index, columns = prices_df.index, prices_df.columns

    def daily_frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, columns=columns, index=index)

    units = daily_frame(records.units)
    effective_weights = daily_frame(records.effective_weights)
    effective_weights['Cash'] = records.cash_balance / records.nav
    nav = pd.Series(records.nav, name='NAV', index=index)
    txn_values = daily_frame(records.txn_values)
    txn_costs = daily_frame(records.txn_costs)
    dividends = daily_frame(records.dividends)
    deposits = pd.Series(records.deposits, name='Deposits', index=index)

    # amount invested in each instrument (trades and costs while held), for the yields
    amounts_invested = -(txn_values + txn_costs).where(units > 0).cumsum().shift(1)
    amounts_invested = amounts_invested.replace(0, np.nan).bfill(limit=1).ffill(limit=1)
    yield_dividends = dividends.div(amounts_invested).resample('Y').sum()
    cum_pnl = prices_df.mul(units).diff().add(dividends).add(txn_values).add(txn_costs).cumsum()
    yield_total_tmp = cum_pnl.resample('Y').last().div(amounts_invested.resample('Y').mean())
    yield_total = yield_total_tmp.diff().fillna(yield_total_tmp)
    yield_total.loc['Total', :] = yield_total.sum()
    # NAV without the effect of deposits and withdrawals, starting at 100
    returns = (nav - deposits).div(nav.shift(1)).sub(1.0).fillna(0.0)
    nav_eff = 100.0 * returns.add(1.0).cumprod().rename('NAV Effective')
    units['Cash'] = records.cash_balance

    return PortfolioBacktestData(
        name=name,
        nav=nav,
        cum_pnl=cum_pnl,
        yield_dividends=yield_dividends,
        yield_total=yield_total,
        units=units,
        target_weights=None,
        effective_weights=effective_weights,
        transaction_value=txn_values,
        transaction_costs=txn_costs,
        prices=prices_df,
        dividends=dividends.resample('M').sum(),
        fx_rates=fx_rates_df,
        deposits=deposits,
        nav_eff=nav_eff,
        close_adj=_complete_adjusted_prices(name, prices_df, close_adj_df),
        id_symbol_map=id_symbol_map,
    )


def _complete_adjusted_prices(name: str, prices_df: pd.DataFrame, close_adj_df: pd.DataFrame | None):
    """Adjusted prices over the backtest period; instruments without them use the backtest prices."""
    if close_adj_df is None:
        return None
    close_adj_df = close_adj_df.reindex(index=prices_df.index).ffill()
    missing_tickers = [t for t in prices_df if t not in close_adj_df]
    if len(missing_tickers) == prices_df.shape[1]:
        logger.warning(
            '%s: no adjusted prices match the instruments of the portfolio, using unadjusted prices for all', name
        )
    close_adj_df[missing_tickers] = prices_df[missing_tickers]
    return close_adj_df[prices_df.columns]
