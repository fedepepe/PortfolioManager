"""Backtest of a strategic asset allocation (config.allocations) and the series it is compared with."""

import logging
from dataclasses import dataclass, field

import pandas as pd

from portfolio_manager.analytics.instruments import stored_adjusted_prices
from portfolio_manager.backtest.adjusted_prices import get_benchmark_adj_prices
from portfolio_manager.backtest.engine import backtest_portfolio
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.backtest.workflows import account_benchmark, load_backtest_data, save_backtest_data
from portfolio_manager.config.accounts import Account
from portfolio_manager.config.allocations import Allocation
from portfolio_manager.config.settings import DEFAULT_DATA_FREQ
from portfolio_manager.optimization.optimizer import MIN_HISTORY_YEARS, compute_weights_optim_portfolio

logger = logging.getLogger(__name__)

BENCHMARK_LABEL = 'Benchmark'
PORTFOLIO_LABEL = 'Portfolio'


@dataclass
class SaaBacktest:
    """Backtest of an allocation: the backtest, the target weights by date (the start for manual weights, every
    optimization date otherwise) and the optimization dates that failed (previous weights kept).
    """

    backtest: PortfolioBacktestData
    weights: pd.DataFrame
    failed_dates: list[pd.Timestamp] = field(default_factory=list)


def saa_backtest_name(account: Account, allocation_name: str) -> str:
    """Name of the saved backtest of an allocation of an account."""
    return f'{account.name} SAA {allocation_name}'


def allocation_prices(account: Account, allocation: Allocation) -> pd.DataFrame:
    """Stored adjusted closes of the instruments of an allocation in the account currency, in their order (ValueError
    if an instrument has none).
    """
    tickers = [i.ticker for i in allocation.instruments]
    prices = stored_adjusted_prices(account, tickers)
    missing = [t for t in tickers if t not in prices.columns]
    if missing:
        raise ValueError(f'No stored prices for {", ".join(missing)}')
    return prices[tickers]


def backtest_allocation(
    account: Account,
    allocation: Allocation,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    save: bool = True,
) -> SaaBacktest:
    """Backtest of an allocation in the account currency, from the first day all its instruments have prices (with
    optimized weights: the history the optimizer needs, MIN_HISTORY_YEARS) or a later start, to the end (the last price
    if None): manual weights rebalanced every rebalancing period, or weights optimized on the first day and at every
    optimization period (from the price history up to then) within the asset class ranges. Saved under
    saa_backtest_name if asked.
    """
    allocation.validate()
    prices = allocation_prices(account, allocation)
    common_start = max(prices[c].first_valid_index() for c in prices.columns)
    if allocation.weight_mode == 'optimize':
        common_start += pd.DateOffset(months=round(12 * MIN_HISTORY_YEARS))
    start = max(pd.Timestamp(start), common_start) if start is not None else common_start
    end = pd.Timestamp(end) if end is not None else prices.index[-1]
    index = prices.index[(prices.index >= start) & (prices.index <= end)]
    if len(index) < 2:
        raise ValueError('Not enough prices to backtest the allocation')
    period_prices = prices.reindex(index).ffill()
    name = saa_backtest_name(account, allocation.name)
    if allocation.weight_mode == 'manual':
        targets = allocation.target_weights()
        weights = pd.DataFrame([targets], index=[index[0]])[prices.columns]
        backtest = backtest_portfolio(
            name=name,
            prices_df=period_prices,
            target_exp=weights.iloc[0].to_list(),
            curr_base=account.currency,
            freq_rebalancing=allocation.rebalancing_freq,
        )
        failed = []
    else:
        settings = allocation.settings
        extra_args = settings.extra_args()
        extra_args['asset_classes'] = allocation.asset_classes()
        extra_args['class_bounds'] = allocation.class_bounds()
        optimized = compute_weights_optim_portfolio(
            allocation_method=settings.method,
            prices=prices[prices.index <= end],  # the history before the start estimates the first weights
            sampling_freq=DEFAULT_DATA_FREQ,
            optimization_freq=settings.optimization_freq,
            extra_args=extra_args,
            start_date=index[0],
            optimize_at_start=True,
        )
        weights, failed = optimized.weights, optimized.failed_dates
        backtest = backtest_portfolio(
            name=name, prices_df=period_prices, target_exp=weights, curr_base=account.currency
        )
    if save:
        save_backtest_data(hist_portfolio_data=backtest)
    logger.info(
        '%s backtested from %s to %s (%s weights)', name, index[0].date(), index[-1].date(), allocation.weight_mode
    )
    return SaaBacktest(backtest=backtest, weights=weights, failed_dates=failed)


def comparison_navs(account: Account, index: pd.DatetimeIndex) -> pd.DataFrame:
    """The series an allocation is compared with over its dates, each starting at 100 on its first date: the benchmark
    of the account (backtested over these dates, with its rebalancing) and the portfolio of the account (its saved
    backtest, where it overlaps the dates; empty without one).
    """
    navs = {}
    benchmark = account_benchmark(account)
    benchmark_prices = get_benchmark_adj_prices(account, benchmark).reindex(index).ffill()
    if not benchmark_prices.isna().all().any():
        backtest = backtest_portfolio(
            name=f'{account.name}_benchmark_saa',
            prices_df=benchmark_prices,
            target_exp=[c.weight for c in benchmark.components],
            curr_base=account.currency,
            freq_rebalancing=benchmark.rebalancing_freq,
        )
        navs[BENCHMARK_LABEL] = backtest.cum_time_weighted_return.add(1.0)
    else:
        logger.warning('Benchmark of %s without prices over the backtest: not compared', account.name)
    try:
        saved = load_backtest_data(name=account.name, fields=['cum_time_weighted_return'])
        portfolio = saved.cum_time_weighted_return.add(1.0)
        navs[PORTFOLIO_LABEL] = portfolio.reindex(index)
    except FileNotFoundError:
        logger.info('%s has no saved backtest: not compared', account.name)
    df = pd.DataFrame(navs, index=index)
    return df.apply(lambda s: 100.0 * s / s.dropna().iloc[0] if s.notna().any() else s)
