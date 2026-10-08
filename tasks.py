"""Maintenance tasks, run from the command line (the dashboard runs the everyday ones with its buttons).

Examples:
    python tasks.py update                      # like the Update button, for every account
    python tasks.py optimize --account degiro_eur
    python tasks.py build-etf-catalog --account degiro_chf
    python tasks.py --help
"""

import argparse
import logging
from collections.abc import Callable

from config.accounts import Accounts
from database.table_definitions import init_db

logger = logging.getLogger(__name__)

ACCOUNT_CHOICES = {account._name_.lower(): account for account in Accounts}


def selected_accounts(args: argparse.Namespace) -> list[Accounts]:
    return list(Accounts) if args.account is None else [ACCOUNT_CHOICES[args.account]]


def update(args: argparse.Namespace):
    """Download the account data from DeGiro, then recompute backtests and performance (like the Update button)."""
    from dashboard.data_service import update_account

    for account in selected_accounts(args):
        update_account(account)
        logger.info('%s updated', account.name)


def backtest(args: argparse.Namespace):
    """Recompute backtests and performance of portfolio and benchmark from the saved DeGiro data."""
    from portfolio.portfolio_backtest import backtest_portfolio_account, backtest_portfolio_benchmark
    from portfolio.portfolio_performance import compute_portfolio_performance

    for account in selected_accounts(args):
        portfolio_data = backtest_portfolio_account(account=account)
        benchmark_data = backtest_portfolio_benchmark(account=account, index=portfolio_data.nav.index)
        compute_portfolio_performance(
            account=account, hist_portfolio_data=portfolio_data, hist_benchmark_data=benchmark_data
        )
        logger.info('%s backtested', account.name)


def optimize(args: argparse.Namespace):
    """Rerun the optimization with the settings of the saved one (defaults if there is none), like Run optimization."""
    from dashboard.data_service import get_optimized_data, run_optimization
    from strategy.strategy_definitions import OptimizationSettings

    for account in selected_accounts(args):
        saved = get_optimized_data(account)
        settings = saved.settings if saved is not None else OptimizationSettings()
        summary = run_optimization(account, settings=settings)
        logger.info('%s optimized (%s): %s', account.name, settings.describe(), summary)


def instruments_performance(args: argparse.Namespace):
    """Performance metrics of the instruments held in the portfolios (all accounts)."""
    from portfolio.instruments_performance import compute_portfolio_instruments_performance

    compute_portfolio_instruments_performance()


def etf_performance(args: argparse.Namespace):
    """Performance metrics of one ETF, from its Yahoo Finance history."""
    from portfolio.instruments_performance import compute_single_etf_performance

    logger.info('Performance of %s:\n%s', args.isin, compute_single_etf_performance(isin=args.isin))


def fetch_product_catalog(args: argparse.Namespace):
    """Download the full DeGiro product catalog into the database (long)."""
    from degiro.products import fetch_full_product_catalog

    fetch_full_product_catalog()


def fetch_etf_catalog(args: argparse.Namespace):
    """Download the Yahoo Finance history of every tradable ETF of the DeGiro catalog (long)."""
    from portfolio.instruments_performance import fetch_etf_catalog_data

    fetch_etf_catalog_data()


def build_etf_catalog(args: argparse.Namespace):
    """Build the ETF catalog of an account from the downloaded data, with its performance metrics."""
    from portfolio.instruments_performance import build_etf_catalog_data

    for account in selected_accounts(args):
        build_etf_catalog_data(account=account)
        logger.info('ETF catalog of %s built', account.name)


def catalog_performance(args: argparse.Namespace):
    """Add the performance metrics to an existing ETF catalog, from its saved data only."""
    from portfolio.instruments_performance import compute_catalog_performance

    for account in selected_accounts(args):
        compute_catalog_performance(account=account)
        logger.info('Performance of the ETF catalog of %s computed', account.name)


# command name: (function, options it takes)
COMMANDS: dict[str, tuple[Callable[[argparse.Namespace], None], tuple[str, ...]]] = {
    'update': (update, ('account',)),
    'backtest': (backtest, ('account',)),
    'optimize': (optimize, ('account',)),
    'instruments-performance': (instruments_performance, ()),
    'etf-performance': (etf_performance, ('isin',)),
    'fetch-product-catalog': (fetch_product_catalog, ()),
    'fetch-etf-catalog': (fetch_etf_catalog, ()),
    'build-etf-catalog': (build_etf_catalog, ('account',)),
    'catalog-performance': (catalog_performance, ('account',)),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Portfolio Manager maintenance tasks')
    parser.add_argument('-v', '--verbose', action='store_true', help='show debug messages')
    subparsers = parser.add_subparsers(dest='command', required=True)
    for name, (function, options) in COMMANDS.items():
        subparser = subparsers.add_parser(name, help=function.__doc__)
        if 'account' in options:
            subparser.add_argument('--account', choices=ACCOUNT_CHOICES, help='one account (default: all)')
        if 'isin' in options:
            subparser.add_argument('--isin', required=True, help='ISIN of the instrument')
        subparser.set_defaults(function=function)
    return parser


def main(argv: list[str] | None = None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    init_db()
    args.function(args)


if __name__ == '__main__':
    main()
