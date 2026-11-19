"""Maintenance tasks, run from the command line (the dashboard runs the everyday ones with its buttons).

Examples:
    python tasks.py update                      # like the Update button, for every account
    python tasks.py refresh-benchmark --account "Portfolio EUR"
    python tasks.py add-account --name "Portfolio USD" --currency USD
    python tasks.py --help
"""

import argparse
import logging
from collections.abc import Callable

from portfolio_manager.config.accounts import Account, Brokers, add_account, get_account, list_accounts
from portfolio_manager.storage.models import init_db
from portfolio_manager.utils.logs import configure_logging

logger = logging.getLogger(__name__)


def selected_accounts(args: argparse.Namespace) -> list[Account]:
    """The account named with --account, or all accounts."""
    if args.account is None:
        return list_accounts()
    account = get_account(args.account)
    if account is None:
        names = ', '.join(f'"{a.name}"' for a in list_accounts()) or 'none yet'
        raise SystemExit(f'Unknown account "{args.account}" (accounts: {names})')
    return [account]


def add_account_task(args: argparse.Namespace):
    """Add a broker account; its credentials go in the credentials folder, in the file shown."""
    account = add_account(
        name=args.name, broker=Brokers(args.broker), credentials_file=args.credentials_file, currency=args.currency
    )
    logger.info('Account %s added: put its credentials in credentials/%s', account.name, account.credentials_file)


def connect_account(args: argparse.Namespace):
    """Log in to the broker: save the base currency of the account and the currency pairs (no other download)."""
    from portfolio_manager.degiro.connection import connect_account as connect

    for account in selected_accounts(args):
        account = connect(account)
        logger.info('%s connected: base currency %s', account.name, account.currency)


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


def show_benchmark(args: argparse.Namespace):
    """Show the benchmark of each account (saved in the database)."""
    from portfolio_manager.backtest.workflows import account_benchmark

    for account in selected_accounts(args):
        try:
            benchmark = account_benchmark(account)
        except ValueError:
            logger.info('%s has no benchmark yet', account.name)
            continue
        logger.info('Benchmark of %s: %s', account.name, benchmark.describe())
        for component in benchmark.components:
            logger.info('  %-8s %5.1f%%  searched as %s', component.label, 100 * component.weight, component.search)


def refresh_benchmark(args: argparse.Namespace):
    """Recompute the benchmark and the performance against it from the saved backtests (no Degiro download)."""
    from portfolio_manager.backtest.workflows import refresh_benchmark as refresh

    for account in selected_accounts(args):
        refresh(account)
        logger.info('Benchmark of %s recomputed', account.name)


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
    """Download the Yahoo Finance history of the listings of the catalog ETFs found by map-etf-catalog (long the first
    time: then only the new days; --full downloads every history again).
    """
    from portfolio_manager.analytics.instruments import fetch_etf_catalog_data

    fetch_etf_catalog_data(full=args.full)


def map_etf_catalog(args: argparse.Namespace):
    """Search the Yahoo Finance listing of the ETFs of the catalog (never searched, failed, not found a month ago,
    found 3 months ago; --retry-not-found: every not found one; --remap-found-not-own-line: the found ones not on their
    own line; --remap: every found one; --remap-before DATE: the found ones searched before, to resume a remap) and
    save the Excel copy of the mapping for review.
    """
    from datetime import datetime

    from portfolio_manager.analytics.instruments import map_etf_catalog as map_catalog

    remap_before = datetime.fromisoformat(args.remap_before) if args.remap_before else None
    map_catalog(
        remap=args.remap,
        retry_not_found=args.retry_not_found,
        remap_before=remap_before,
        remap_found_not_own_line=args.remap_found_not_own_line,
    )


def remap_portfolio_listings(args: argparse.Namespace):
    """Search again the Yahoo Finance listing of the products traded in the accounts and show the changes (--apply:
    save them).
    """
    from portfolio_manager.analytics.instruments import remap_portfolio_listings as remap

    changes = remap(apply=args.apply)
    for change in changes.itertuples():
        fix = f', prices in {change.currency}' if change.currency else ''
        logger.info(
            '%s (%s): %s -> %s (%s%s)', change.symbol, change.product_id, change.stored, change.found, change.rule, fix
        )
    logger.info('%d listings %s', len(changes), 'changed' if args.apply else 'would change (--apply to save them)')


def refresh_yahoo_history(args: argparse.Namespace):
    """Bring the stored Yahoo Finance history of every listing up to date (--full: download every history again)."""
    from portfolio_manager.market_data.history import refresh_history
    from portfolio_manager.storage.queries import query_yahoo_tickers_with_history

    tickers = query_yahoo_tickers_with_history()
    for n, ticker in enumerate(tickers):
        logger.info('(%d/%d) %s: %s', n + 1, len(tickers), ticker, refresh_history(ticker, full=args.full))


def fetch_taa_data(args: argparse.Namespace):
    """Bring the data of the Tactical Asset Allocation research up to date: the whole history of its instruments in use
    (indices, sectors, bonds, commodities, currencies) and the interest rates in use (--full: download everything
    again, e.g. the whole history of a listing stored with fewer years by another page).
    """
    import time
    from collections import Counter

    from portfolio_manager.market_data.history import listing_currencies, refresh_history, refresh_info
    from portfolio_manager.market_data.rates import update_rates
    from portfolio_manager.storage.queries import query_taa_instruments

    tickers = [instrument.ticker for instrument in query_taa_instruments()]
    known = listing_currencies(tickers)
    results = Counter()
    for n, ticker in enumerate(tickers):
        try:
            result = refresh_history(ticker, full=args.full)
            if ticker not in known:
                refresh_info(ticker)
        except Exception as e:  # any download problem: the stored history is kept
            logger.warning('Yahoo Finance download failed for %s: %s', ticker, e)
            result = 'failed'
        results[result] += 1
        logger.debug('(%d/%d) %s: %s', n + 1, len(tickers), ticker, result)
        time.sleep(0.2)  # spread the requests to Yahoo Finance
    logger.info('Instruments downloaded: %s', ', '.join(f'{k} {v}' for k, v in sorted(results.items())))
    update_rates(full=args.full)


def build_etf_catalog(args: argparse.Namespace):
    """Build the ETF catalog of an account from the downloaded data, with its performance metrics."""
    from portfolio_manager.analytics.instruments import build_etf_catalog_data

    for account in selected_accounts(args):
        build_etf_catalog_data(account=account)
        logger.info('ETF catalog of %s built', account.name)


def refresh_markets(args: argparse.Namespace):
    """Download the new days of the instruments of the Markets page (sectors, indices, commodities, currencies)."""
    from portfolio_manager.analytics.markets import refresh_market_data

    refresh_market_data()


def classify_etfs(args: argparse.Namespace):
    """Classify the ETFs of the catalog by asset class (Equity, Fixed Income, ...) from Degiro's ETF list and rules on
    their names; saves the classes and results/etf_classes.xlsx for review (needs the Degiro login).
    """
    from portfolio_manager.analytics.classification import classify_etfs as classify
    from portfolio_manager.degiro.connection import get_degiro_connection

    classify(get_degiro_connection())


def convert_results(args: argparse.Namespace):
    """Convert the backtests and results saved as Excel files into Parquet tables (archives the Excel copies no
    longer needed).
    """
    import datetime
    import os
    import shutil

    import pandas as pd

    from portfolio_manager.analytics.performance import RESULTS, load_performance_data, save_performance_tables
    from portfolio_manager.backtest.workflows import (
        BACKTESTS,
        load_backtest_data,
        load_backtest_data_excel,
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
        names = [account.name, f'{account.name}_benchmark']
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


def import_degiro_excel(args: argparse.Namespace):
    """Import the Degiro data saved as Excel files (transactions, cash movements, products, prices) into the database:
    stored transactions, movements and prices are kept, products are updated.
    """
    import ast

    import pandas as pd

    from portfolio_manager.config.settings import DATA_DIR, FX_RATES_CHART_FILE_NAME, PRODUCTS_CHART_FILE_NAME
    from portfolio_manager.degiro.charts import charts_file_name, load_portfolio_charts
    from portfolio_manager.degiro.products import load_portfolio_products, products_file_name
    from portfolio_manager.degiro.transactions import (
        load_account_movements,
        load_tx_history,
        movements_file_name,
        tx_history_file_name,
    )
    from portfolio_manager.storage.files import load_df_from_excel
    from portfolio_manager.storage.models import DegiroCashMovement, DegiroTransaction
    from portfolio_manager.storage.queries import insert_degiro_hist, upsert_degiro_records, upsert_products

    def text(value) -> str:
        if pd.isna(value):
            return ''
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)

    def product_fields_as_text(df: pd.DataFrame) -> pd.DataFrame:
        # the product fields used by the application; the vwd id is a number or a text key: compared as text
        fields = ['id', 'symbol', 'name', 'isin', 'currency', 'vwd_id', 'vwd_identifier_type', 'exchange_id']
        return df[fields].applymap(text)

    for account in selected_accounts(args):
        tx_hist_df = load_df_from_excel(tx_history_file_name(account), folder_name=DATA_DIR)
        upsert_degiro_records(DegiroTransaction, account.name, tx_hist_df, overwrite=False)
        movements_df = load_df_from_excel(movements_file_name(account), folder_name=DATA_DIR)
        upsert_degiro_records(DegiroCashMovement, account.name, movements_df, overwrite=False)
        products_df = load_df_from_excel(products_file_name(account), folder_name=DATA_DIR)
        # list fields were saved as their text representation
        for column in ['buy_order_types', 'order_time_types', 'product_bit_types', 'sell_order_types']:
            products_df[column] = [ast.literal_eval(v) if isinstance(v, str) else v for v in products_df[column]]
        upsert_products(products_df)
        for chart_name in [PRODUCTS_CHART_FILE_NAME, FX_RATES_CHART_FILE_NAME]:
            chart_df = load_df_from_excel(charts_file_name(account, chart_name), folder_name=DATA_DIR)
            for product_id in chart_df.columns:
                insert_degiro_hist(product_id, chart_df[[product_id]].set_axis(['price'], axis=1), overwrite=False)
            # stored prices are kept: report where they differ from the Excel file
            stored = load_portfolio_charts(account, chart_name=chart_name).reindex_like(chart_df)
            differ = ~((stored - chart_df).abs() <= 1e-9 * chart_df.abs()) & chart_df.notna()
            logger.info(
                '%s %s: %d prices, %d differ from the database (kept), on %s',
                account.name,
                chart_name,
                int(chart_df.notna().sum().sum()),
                int(differ.sum().sum()),
                sorted({str(d.date()) for d in chart_df.index[differ.any(axis=1)]})[:10],
            )
        # the database holds at least the records of the Excel files, with the same values (records at the same time
        # are sorted by id in the database)
        for loaded, saved in [(load_tx_history(account), tx_hist_df), (load_account_movements(account), movements_df)]:
            saved = saved.reset_index().sort_values(['date', 'id'], kind='stable').set_index('date')
            loaded = loaded.loc[loaded['id'].isin(saved['id']), saved.columns]
            pd.testing.assert_frame_equal(loaded, saved, check_dtype=False)
        pd.testing.assert_frame_equal(
            product_fields_as_text(load_portfolio_products(account).loc[products_df.index]),
            product_fields_as_text(products_df),
        )
        logger.info(
            '%s: %d transactions, %d cash movements and %d products imported',
            account.name,
            len(tx_hist_df),
            len(movements_df),
            len(products_df),
        )


# command name: (function, options it takes)
COMMANDS: dict[str, tuple[Callable[[argparse.Namespace], None], tuple[str, ...]]] = {
    'add-account': (add_account_task, ('new_account',)),
    'connect-account': (connect_account, ('account',)),
    'update': (update, ('account',)),
    'backtest': (backtest, ('account',)),
    'show-benchmark': (show_benchmark, ('account',)),
    'refresh-benchmark': (refresh_benchmark, ('account',)),
    'instruments-performance': (instruments_performance, ()),
    'etf-performance': (etf_performance, ('isin',)),
    'fetch-product-catalog': (fetch_product_catalog, ()),
    'fetch-etf-catalog': (fetch_etf_catalog, ('full',)),
    'refresh-yahoo-history': (refresh_yahoo_history, ('full',)),
    'map-etf-catalog': (map_etf_catalog, ('remap',)),
    'remap-portfolio-listings': (remap_portfolio_listings, ('apply',)),
    'build-etf-catalog': (build_etf_catalog, ('account',)),
    'classify-etfs': (classify_etfs, ()),
    'refresh-markets': (refresh_markets, ()),
    'fetch-taa-data': (fetch_taa_data, ('full',)),
    'convert-results': (convert_results, ('account',)),
    'import-degiro-excel': (import_degiro_excel, ('account',)),
}


def build_parser() -> argparse.ArgumentParser:
    """Command-line parser: one subcommand per task, with the options it takes."""
    parser = argparse.ArgumentParser(description='Portfolio Manager maintenance tasks')
    parser.add_argument('-v', '--verbose', action='store_true', help='show debug messages')
    subparsers = parser.add_subparsers(dest='command', required=True)
    for name, (function, options) in COMMANDS.items():
        subparser = subparsers.add_parser(name, help=function.__doc__)
        if 'account' in options:
            subparser.add_argument('--account', help='name of one account (default: all)')
        if 'new_account' in options:
            subparser.add_argument('--name', required=True, help='name of the account (the portfolio name)')
            subparser.add_argument('--broker', default=Brokers.DEGIRO.value, choices=[b.value for b in Brokers])
            subparser.add_argument('--credentials-file', help='file in the credentials folder (default: from the name)')
            subparser.add_argument('--currency', help='base currency, e.g. EUR')
        if 'remap' in options:
            subparser.add_argument('--remap', action='store_true', help='search again the products with a listing')
            subparser.add_argument(
                '--retry-not-found', action='store_true', help='search again every product not found'
            )
            subparser.add_argument(
                '--remap-before', help='search again the products searched before this time (resumes a remap)'
            )
            subparser.add_argument(
                '--remap-found-not-own-line',
                action='store_true',
                help='search again the products found, but not on their own line',
            )
        if 'apply' in options:
            subparser.add_argument('--apply', action='store_true', help='apply the changes (default: only show them)')
        if 'full' in options:
            subparser.add_argument('--full', action='store_true', help='download every history again')
        if 'isin' in options:
            subparser.add_argument('--isin', required=True, help='ISIN of the instrument')
        subparser.set_defaults(function=function)
    return parser


def main(argv: list[str] | None = None):
    """Run the task given on the command line."""
    args = build_parser().parse_args(argv)
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    init_db()
    args.function(args)


if __name__ == '__main__':
    main()
