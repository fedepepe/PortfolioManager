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

from portfolio_manager.config.accounts import Accounts
from portfolio_manager.storage.models import init_db

logger = logging.getLogger(__name__)

ACCOUNT_CHOICES = {account._name_.lower(): account for account in Accounts}


def selected_accounts(args: argparse.Namespace) -> list[Accounts]:
    """The account given with --account, or all accounts."""
    return list(Accounts) if args.account is None else [ACCOUNT_CHOICES[args.account]]


def update(args: argparse.Namespace):
    """Download the account data from Degiro, then recompute backtests and performance (like the Update button)."""
    from portfolio_manager.dashboard.data_service import update_account

    for account in selected_accounts(args):
        update_account(account)
        logger.info('%s updated', account.name)


def backtest(args: argparse.Namespace):
    """Recompute backtests and performance of portfolio and benchmark from the saved Degiro data."""
    from portfolio_manager.analytics.performance import compute_portfolio_performance
    from portfolio_manager.backtest.workflows import backtest_portfolio_account, backtest_portfolio_benchmark

    for account in selected_accounts(args):
        portfolio_data = backtest_portfolio_account(account=account)
        benchmark_data = backtest_portfolio_benchmark(account=account, index=portfolio_data.nav.index)
        compute_portfolio_performance(
            account=account, hist_portfolio_data=portfolio_data, hist_benchmark_data=benchmark_data
        )
        logger.info('%s backtested', account.name)


def optimize(args: argparse.Namespace):
    """Rerun the optimization with the settings of the saved one (defaults if there is none), like Run optimization."""
    from portfolio_manager.dashboard.data_service import get_optimized_data, run_optimization
    from portfolio_manager.optimization.settings import OptimizationSettings

    for account in selected_accounts(args):
        saved = get_optimized_data(account)
        settings = saved.settings if saved is not None else OptimizationSettings()
        summary = run_optimization(account, settings=settings)
        logger.info('%s optimized (%s): %s', account.name, settings.describe(), summary)


def instruments_performance(args: argparse.Namespace):
    """Performance metrics of the instruments held in the portfolios (all accounts)."""
    from portfolio_manager.analytics.instruments import compute_portfolio_instruments_performance

    compute_portfolio_instruments_performance()


def etf_performance(args: argparse.Namespace):
    """Performance metrics of one ETF, from its Yahoo Finance history."""
    from portfolio_manager.analytics.instruments import compute_single_etf_performance

    logger.info('Performance of %s:\n%s', args.isin, compute_single_etf_performance(isin=args.isin))


def fetch_product_catalog(args: argparse.Namespace):
    """Download the full Degiro product catalog into the database (long)."""
    from portfolio_manager.degiro.products import fetch_full_product_catalog

    fetch_full_product_catalog()


def fetch_etf_catalog(args: argparse.Namespace):
    """Download the Yahoo Finance history of every tradable ETF of the Degiro catalog (long)."""
    from portfolio_manager.analytics.instruments import fetch_etf_catalog_data

    fetch_etf_catalog_data()


def build_etf_catalog(args: argparse.Namespace):
    """Build the ETF catalog of an account from the downloaded data, with its performance metrics."""
    from portfolio_manager.analytics.instruments import build_etf_catalog_data

    for account in selected_accounts(args):
        build_etf_catalog_data(account=account)
        logger.info('ETF catalog of %s built', account.name)


def catalog_performance(args: argparse.Namespace):
    """Add the performance metrics to an existing ETF catalog, from its saved data only."""
    from portfolio_manager.analytics.instruments import compute_catalog_performance

    for account in selected_accounts(args):
        compute_catalog_performance(account=account)
        logger.info('Performance of the ETF catalog of %s computed', account.name)


def convert_results(args: argparse.Namespace):
    """Convert the results saved as Excel files into Parquet tables (archives the Excel copies no longer needed)."""
    import datetime
    import os
    import shutil

    import pandas as pd

    from portfolio_manager.analytics.instruments import (
        CATALOGS,
        catalog_file_name,
        load_etf_catalog_data,
        save_etf_catalog_tables,
    )
    from portfolio_manager.analytics.performance import RESULTS, load_performance_data, save_performance_tables
    from portfolio_manager.backtest.workflows import (
        BACKTESTS,
        load_backtest_data,
        load_backtest_data_excel,
        optimized_portfolio_name,
        save_backtest_tables,
    )
    from portfolio_manager.config.settings import DATA_DIR, PROJECT_DIR, RESULTS_DIR
    from portfolio_manager.storage.files import load_df_dict_from_excel, to_file_name
    from portfolio_manager.storage.tables import derived_folder, has_tables

    def assert_same(expected: dict, loaded: dict):
        assert expected.keys() == loaded.keys(), (expected.keys(), loaded.keys())
        for key, table in expected.items():
            if isinstance(table, pd.Series):
                pd.testing.assert_series_equal(table, loaded[key], check_freq=False)
            else:
                pd.testing.assert_frame_equal(table, loaded[key], check_freq=False)

    archive_dir = os.path.join(
        os.path.dirname(PROJECT_DIR), f'{os.path.basename(PROJECT_DIR)}_archive', str(datetime.date.today()), 'data'
    )
    for account in selected_accounts(args):
        names = [account.name, f'{account.name}_benchmark', optimized_portfolio_name(account)]
        for name in names:
            excel_file = os.path.join(DATA_DIR, f'{to_file_name(name)}.xlsx')
            if os.path.isfile(excel_file) and not has_tables(derived_folder(BACKTESTS, name)):
                data = load_backtest_data_excel(name)
                save_backtest_tables(data, saved_at=os.path.getmtime(excel_file))
                loaded = load_backtest_data(name)
                assert_same(
                    {k: v for k, v in vars(data).items() if isinstance(v, pd.DataFrame | pd.Series)},
                    {k: v for k, v in vars(loaded).items() if isinstance(v, pd.DataFrame | pd.Series)},
                )
                logger.info('Backtest %s converted', name)
                # the Excel copy with symbols (_visual) stays for inspection: the one with product ids is archived
                if os.path.isfile(os.path.join(DATA_DIR, f'{to_file_name(name)}_visual.xlsx')):
                    os.makedirs(archive_dir, exist_ok=True)
                    shutil.move(excel_file, os.path.join(archive_dir, os.path.basename(excel_file)))
                    logger.info('%s archived in %s', os.path.basename(excel_file), archive_dir)
            excel_file = os.path.join(RESULTS_DIR, f'{to_file_name(name)}.xlsx')
            if os.path.isfile(excel_file) and not has_tables(derived_folder(RESULTS, name)):
                results = load_df_dict_from_excel(file_name=name, folder_name=RESULTS_DIR)
                save_performance_tables(results, name, saved_at=os.path.getmtime(excel_file))
                assert_same(results, load_performance_data(name))
                logger.info('Performance results %s converted', name)
        excel_file = os.path.join(RESULTS_DIR, f'{to_file_name(catalog_file_name(account))}.xlsx')
        if os.path.isfile(excel_file) and not has_tables(derived_folder(CATALOGS, catalog_file_name(account))):
            catalog = load_df_dict_from_excel(file_name=catalog_file_name(account), folder_name=RESULTS_DIR)
            save_etf_catalog_tables(account, catalog)
            assert_same(catalog, {str(k): v for k, v in load_etf_catalog_data(account).items()})
            logger.info('ETF catalog of %s converted', account.name)


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
    'convert-results': (convert_results, ('account',)),
}


def build_parser() -> argparse.ArgumentParser:
    """Command-line parser: one subcommand per task, with the options it takes."""
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
    """Run the task given on the command line."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    init_db()
    args.function(args)


if __name__ == '__main__':
    main()
