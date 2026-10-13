"""Instrument data from Yahoo Finance: prices in the account currency, performance and the ETF catalog of each
account.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta

import pandas as pd

from portfolio_manager.analytics.metrics import Metrics, PerfDataTabs, compute_portfolio_metrics
from portfolio_manager.config.accounts import Account, list_accounts
from portfolio_manager.config.settings import RESULTS_DIR
from portfolio_manager.degiro.charts import load_fx_rates
from portfolio_manager.degiro.definitions import Exchanges, ProductTypes, exchange_name, yahoo_suffix
from portfolio_manager.degiro.products import load_portfolio_products
from portfolio_manager.market_data.history import (
    Listing,
    ListingSearches,
    find_listing,
    listing_currencies,
    load_adjusted_closes,
    major_currency,
    refresh_history,
    refresh_info,
    search_listing,
    store_listing,
    traded_value,
)
from portfolio_manager.market_data.yahoo import YFinInfoCols
from portfolio_manager.storage.files import PD_DATA_TYPES, save_df_dict_to_excel, save_df_to_excel
from portfolio_manager.storage.models import Product
from portfolio_manager.storage.queries import (
    query_account_product_ids,
    query_degiro_yahoo_map,
    query_etf_catalog,
    query_listing_searches,
    query_products,
    query_yahoo_instruments,
    save_accepted_own_line,
    save_etf_catalog,
    save_listing_search,
)

logger = logging.getLogger(__name__)

MAX_ETF_CATALOG_SIZE = 250
# Degiro exchanges of the ETFs of the catalog
CATALOG_EXCHANGES = [Exchanges.XET, Exchanges.SWX, Exchanges.MIL, Exchanges.EAM]
TRADED_VALUE_PERIOD = pd.DateOffset(months=3)  # average daily traded value over the last 3 calendar months
MAX_DAYS_WITHOUT_PRICE = 30  # listings without a price for longer are left out of the catalog
PRODUCT_SEARCH_RETRY = timedelta(days=30)  # a product whose listing was not found is searched again after this
LISTING_REVIEW_FILE = 'yahoo_mapping'  # Excel copy of the mapping of the Degiro products, for review
# Yahoo information fields (rows of the information tables of the catalogs) and their columns in the database
INFO_COLUMNS = {
    YFinInfoCols.currency.value: 'currency',
    YFinInfoCols.isin.value: 'isin',
    YFinInfoCols.name_short.value: 'name_short',
    YFinInfoCols.name_long.value: 'name_long',
    YFinInfoCols.quote_type.value: 'quote_type',
    YFinInfoCols.exchange.value: 'exchange',
    YFinInfoCols.market.value: 'market',
    YFinInfoCols.region.value: 'region',
}


# metrics of the catalog ETFs and their columns in the etf_catalog table
CATALOG_METRICS = {
    Metrics.TOTAL_RETURN: 'total_return',
    Metrics.PA_RETURN: 'pa_return',
    Metrics.LAST_YEAR_RETURN: 'return_1y',
    Metrics.ANN_3Y_RETURN: 'return_3y',
    Metrics.ANN_5Y_RETURN: 'return_5y',
    Metrics.VOLATILITY: 'volatility',
    Metrics.SHARPE_RATIO: 'sharpe_ratio',
    Metrics.SORTINO_RATIO: 'sortino_ratio',
    Metrics.BEST_MONTH: 'best_month',
    Metrics.WORST_MONTH: 'worst_month',
    Metrics.MAX_DD: 'max_drawdown',
    Metrics.SKEWNESS: 'skewness',
}


class InstrPerfTableCols:
    """Columns of the instruments table."""

    ticker = 'Ticker'
    isin = 'ISIN'
    name = 'Name'
    volume = 'Traded value (3M)'  # average daily traded value of the last 3 months, in the currency of the listing
    currency = 'Currency'


def prices_to_base_curr(account: Account, price_df: pd.DataFrame, curr_info: PD_DATA_TYPES | list[str]) -> pd.DataFrame:
    """Amounts (prices, traded values) converted to the account currency, given the Yahoo currency of each column
    (amounts quoted in hundredths of a currency, e.g. pence, are converted too).
    """
    if not isinstance(curr_info, pd.Series | list):
        raise TypeError
    majors = [major_currency(c) if isinstance(c, str) and c else (None, 1.0) for c in curr_info]
    curr_foreign_lst = sorted({c for c, _ in majors if c is not None and c != account.currency})
    fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst, account=account, index=price_df.index)
    fx_rates_df = fx_rates_df.reindex(index=price_df.index).ffill()
    # convert to base currency; a ticker appearing in several columns (e.g. several exchanges) is averaged
    price_base_df = pd.DataFrame()
    for ticker, (curr, factor) in zip(price_df.columns, majors, strict=True):
        if ticker in price_base_df:
            continue
        try:
            fx_rate = fx_rates_df.loc[price_df.index, f'{curr}/{account.currency}']
        except KeyError:
            logger.warning('Missing foreign exchange historical time series for %s/%s', curr, account.currency)
            continue
        ser = price_df[ticker]
        if isinstance(ser, pd.DataFrame):
            ser = ser.mean(axis=1)
        ser = ser.mul(fx_rate, axis=0).mul(factor).rename(ticker)
        price_base_df = pd.concat([price_base_df, ser], axis=1)
    price_base_df.index = pd.to_datetime(price_base_df.index)
    price_base_df = price_base_df.sort_index()
    return price_base_df


def stored_adjusted_prices(account: Account, tickers: list[str]) -> pd.DataFrame:
    """Stored adjusted closes of Yahoo Finance listings in the account currency: date x ticker (listings without
    history or currency are left out).
    """
    closes = load_adjusted_closes(tickers)
    currencies = listing_currencies(list(closes.columns))
    tickers = [t for t in closes.columns if t in currencies]
    if not tickers:
        return pd.DataFrame()
    return prices_to_base_curr(account=account, price_df=closes[tickers], curr_info=[currencies[t] for t in tickers])


def instruments_info(tickers: list[str]) -> pd.DataFrame:
    """Information of Yahoo Finance listings: Yahoo field x ticker (the layout of the catalog tables)."""
    df = query_yahoo_instruments(tickers).reindex(tickers)
    info = pd.DataFrame({field: df[column] for field, column in INFO_COLUMNS.items()}).T
    info.loc[YFinInfoCols.symbol.value] = tickers
    return info


def fetch_portfolio_instr_adj_prices(account: Account) -> pd.DataFrame:
    """Adjusted prices of the products of an account, in the account currency: date x product symbol."""
    from portfolio_manager.backtest.adjusted_prices import get_portfolio_adj_prices

    prices = get_portfolio_adj_prices(account=account).prices
    symbols = load_portfolio_products(account=account).set_index('id')['symbol']
    return prices.rename(columns=symbols.to_dict())


def compute_product_performance(adj_close_df: PD_DATA_TYPES, prod_info_df: PD_DATA_TYPES | None = None) -> pd.DataFrame:
    """Performance metrics of each price series: one row per ticker (with ISIN and name if the information is given)."""
    perf_metrics_df = pd.DataFrame()
    for ticker in adj_close_df.columns:
        isin_str = f' ({prod_info_df.loc[YFinInfoCols.isin.value, ticker]})' if prod_info_df is not None else ''
        logger.debug('Computing performance metrics for %s%s', ticker, isin_str)
        # compute performance metrics
        try:
            results_dict = compute_portfolio_metrics(
                nav=adj_close_df[ticker].dropna(), compute_hist_metrics=False, print_results=False
            )
        except ValueError:
            continue
        if prod_info_df is not None:
            isin = prod_info_df.loc[YFinInfoCols.isin.value, ticker]
            results_dict[PerfDataTabs.RISK_METRICS][InstrPerfTableCols.isin] = isin
            name = prod_info_df.loc[YFinInfoCols.name_long.value, ticker]
            results_dict[PerfDataTabs.RISK_METRICS][InstrPerfTableCols.name] = name
        perf_metrics_df = pd.concat([perf_metrics_df, results_dict[PerfDataTabs.RISK_METRICS]], axis=1)
    perf_metrics_df = perf_metrics_df.T.copy()
    perf_metrics_df.index.name = InstrPerfTableCols.ticker
    perf_metrics_df = perf_metrics_df.reset_index().copy()
    return perf_metrics_df


def compute_single_etf_performance(isin: str) -> pd.DataFrame:
    """Performance metrics of one ETF (its Yahoo Finance listing is searched, downloaded and stored)."""
    # ticker and name from the Degiro catalog (if listed) help picking the right Yahoo Finance listing
    prod_df = query_products(product_isin=isin, product_type=ProductTypes.ETF)
    ticker = prod_df[Product.symbol.name].iloc[0] if not prod_df.empty else None
    name = prod_df[Product.name.name].iloc[0] if not prod_df.empty else None
    listing = search_listing(search=isin, ticker=ticker, name=name)
    if listing is None:
        return pd.DataFrame()
    df = compute_product_performance(
        adj_close_df=load_adjusted_closes([listing]), prod_info_df=instruments_info([listing])
    )
    logger.debug('Performance of %s:\n%s', isin, df)
    return df


def compute_portfolio_instruments_performance():
    """Performance metrics of the instruments of every account."""
    for account in list_accounts():
        close_adj_df = fetch_portfolio_instr_adj_prices(account=account)
        perf_metrics_df = pd.DataFrame()
        for instr in close_adj_df.columns:
            results_dict = compute_portfolio_metrics(nav=close_adj_df[instr])
            perf_metrics_df = pd.concat([perf_metrics_df, results_dict[PerfDataTabs.RISK_METRICS]], axis=1)
        save_df_dict_to_excel(
            df_dict={PerfDataTabs.RISK_METRICS: perf_metrics_df, PerfDataTabs.PRICES: close_adj_df},
            folder_name=RESULTS_DIR,
            file_name=f'{account.name}_instr',
        )


def catalog_products() -> pd.DataFrame:
    """Tradable ETFs of the Degiro catalog on the exchanges of the catalog, one per ISIN and symbol."""
    etf_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)
    etf_info_df = etf_info_df[etf_info_df[Product.exchange_id.name].isin([x.value for x in CATALOG_EXCHANGES])]
    return etf_info_df.drop_duplicates(subset=['isin', 'symbol'], keep='first')


def traded_product_ids() -> set[int]:
    """Products traded in any account."""
    return {p for account in list_accounts() for p in query_account_product_ids(account.name)}


def search_product_listing(
    product,
    searches: ListingSearches | None = None,
    decided: pd.DataFrame | None = None,
    confirm: Callable | None = None,
) -> Listing:
    """Find the Yahoo Finance listing of a Degiro product (a row of the products table): its own line first (see
    find_listing), with the own lines accepted before (rows of query_listing_searches by product); confirm(product,
    doubt) asks the user about a doubtful own line (see OwnLineDoubt). A failed search is a result too.
    """
    accepted = None
    if (
        decided is not None
        and product.id in decided.index
        and decided.loc[product.id, 'own_line_decision'] == 'accepted'
    ):
        accepted = decided.loc[product.id, 'own_line']
    try:
        return find_listing(
            search=product.isin,
            ticker=product.symbol,
            name=product.name,
            exchange=yahoo_suffix(product.exchange_id),
            currency=product.currency,
            accepted=accepted,
            confirm=(lambda doubt: confirm(product, doubt)) if confirm is not None else None,
            searches=searches,
        )
    except Exception as e:  # any download problem: searched again at the next run
        logger.warning('Yahoo Finance search failed for %s (%s): %s', product.symbol, product.isin, e)
        return Listing(None, 'failed')


def products_to_search(
    products: pd.DataFrame,
    remap: bool = False,
    retry: bool = False,
    remap_before: datetime | None = None,
    remap_not_own_line: bool = False,
) -> pd.DataFrame:
    """The products whose listing must be searched: never searched, failed, not found PRODUCT_SEARCH_RETRY ago (or
    with retry), found only with remap, with remap_before if searched before then (to resume a remap), or with
    remap_not_own_line if not on their own line.
    """
    searches = query_listing_searches(products['id'].to_list()).set_index('product_id')
    now = pd.Timestamp(datetime.now())

    def due(product_id: int) -> bool:
        if product_id not in searches.index:
            return True
        search = searches.loc[product_id]
        if search['status'] == 'failed':
            return True
        if search['status'] == 'not found':
            return retry or pd.isna(search['searched_at']) or now - search['searched_at'] >= PRODUCT_SEARCH_RETRY
        if remap_before is not None:
            return pd.isna(search['searched_at']) or search['searched_at'] < pd.Timestamp(remap_before)
        if remap_not_own_line:
            return search['rule'] not in ('own line', 'own line (confirmed)')
        return remap

    return products[products['id'].map(due)]


def map_listings(
    products: pd.DataFrame,
    remap: bool = False,
    retry: bool = False,
    remap_before: datetime | None = None,
    remap_not_own_line: bool = False,
    confirm: Callable | None = None,
) -> Counter:
    """Search and save the Yahoo Finance listing of the products to search (see products_to_search); a failed search
    keeps the listing found before; confirm(product, doubt) asks the user about doubtful own lines (without it, they
    are only reported). Returns the count of each status.
    """
    todo = products_to_search(
        products, remap=remap, retry=retry, remap_before=remap_before, remap_not_own_line=remap_not_own_line
    )
    found_before = query_degiro_yahoo_map(todo['id'].to_list())
    decided = query_listing_searches(todo['id'].to_list()).set_index('product_id')
    searches, results = ListingSearches(), Counter()
    for n, product in enumerate(todo.itertuples()):
        listing = search_product_listing(product, searches, decided, confirm)
        if listing.status != 'failed' or product.id not in found_before:
            save_listing_search([product.id], listing.ticker, listing.status, listing.rule)
        save_own_line_decision(product.id, listing, asked=confirm is not None)
        results[listing.status] += 1
        if listing.proposal is not None and listing.rule != 'own line (confirmed)':
            results['own line not used'] += 1
        logger.info(
            '%5.1f%% (%d/%d) %s %s %s %s %s: %s',
            100 * (n + 1) / len(todo),
            n + 1,
            len(todo),
            product.id,
            exchange_name(product.exchange_id),
            product.isin,
            product.symbol,
            listing.ticker or '',
            listing.rule or listing.status,
        )
    logger.info('Listings searched: %s', ', '.join(f'{k} {v}' for k, v in results.items()) or 'none due')
    return results


def save_own_line_decision(product_id: int, listing: Listing, asked: bool):
    """Remember an own line accepted by the user; report a doubtful own line not used (rejected, or not asked: it is
    asked again at the next search run from a terminal).
    """
    if listing.proposal is None:
        return
    if listing.rule == 'own line (confirmed)':
        save_accepted_own_line(product_id, listing.ticker, listing.proposal_name)
    elif asked:
        logger.info('Own line %s rejected: %s is used', listing.proposal, listing.ticker or 'no listing')
    else:
        logger.warning(
            'Own line %s (%s) of product %s has another name: not used, to confirm in a search run from a terminal',
            listing.proposal,
            listing.proposal_name,
            product_id,
        )


def map_etf_catalog(
    remap: bool = False,
    retry: bool = False,
    remap_before: datetime | None = None,
    remap_not_own_line: bool = False,
    confirm: Callable | None = None,
) -> Counter:
    """Search the Yahoo Finance listing of the ETFs of the catalog (see map_listings), then save the Excel copy of the
    mapping for review. The products traded in an account keep their listing: see remap_portfolio_listings.
    """
    products = catalog_products()
    products = products[~products['id'].isin(traded_product_ids())]
    results = map_listings(
        products,
        remap=remap,
        retry=retry,
        remap_before=remap_before,
        remap_not_own_line=remap_not_own_line,
        confirm=confirm,
    )
    export_listing_review()
    return results


def remap_portfolio_listings(apply: bool = False, confirm: Callable | None = None) -> pd.DataFrame:
    """Search again the listing of the products traded in the accounts: the products whose listing would change
    (product_id, symbol, name, stored, found, rule); with apply, the new listings are saved and downloaded (and the
    own lines accepted through confirm remembered).
    """
    products = query_products(product_id=sorted(traded_product_ids()))
    stored = query_degiro_yahoo_map(products['id'].to_list())
    decided = query_listing_searches(products['id'].to_list()).set_index('product_id')
    searches, changes = ListingSearches(), []
    for product in products.itertuples():
        listing = search_product_listing(product, searches, decided, confirm)
        if listing.status != 'failed' and listing.ticker != stored.get(product.id):
            changes.append(
                (product.id, product.symbol, product.name, stored.get(product.id), listing.ticker, listing.rule)
            )
            if apply:
                save_listing_search([product.id], listing.ticker, listing.status, listing.rule)
                if listing.ticker is not None:
                    store_listing(listing.ticker, isin=product.isin)
        if apply:
            save_own_line_decision(product.id, listing, asked=confirm is not None)
    return pd.DataFrame(changes, columns=['product_id', 'symbol', 'name', 'stored', 'found', 'rule'])


def export_listing_review():
    """Excel copy of the mapping of the Degiro products to Yahoo Finance listings, for review (results folder)."""
    searches = query_listing_searches()
    products = query_products(product_id=searches['product_id'].to_list()).reset_index(drop=True)
    instruments = query_yahoo_instruments(sorted(searches['ticker'].dropna().unique()))
    review = searches.merge(
        products[['id', 'exchange_id', 'symbol', 'isin', 'name']], left_on='product_id', right_on='id', how='left'
    )
    review['exchange'] = review['exchange_id'].map(exchange_name)
    review['yahoo_name'] = review['ticker'].map(instruments['name_long'].fillna(instruments['name_short']))
    review['yahoo_currency'] = review['ticker'].map(instruments['currency'])
    columns = ['product_id', 'exchange', 'symbol', 'isin', 'name', 'ticker', 'yahoo_name', 'yahoo_currency']
    review = review[columns + ['status', 'rule', 'searched_at']].sort_values(['status', 'isin'])
    save_df_to_excel(df=review.set_index('product_id'), file_name=LISTING_REVIEW_FILE, folder_name=RESULTS_DIR)


def fetch_etf_catalog_data(full: bool = False):
    """Download the Yahoo Finance history of the listings of the catalog ETFs (see map_etf_catalog): the whole history
    of a new listing (or of every listing with full), otherwise the new days. An interrupted run resumes where it
    stopped.
    """
    listings = query_degiro_yahoo_map(catalog_products()['id'].to_list())
    isins = query_products(product_id=list(listings)).set_index('id')['isin']
    isin_by_ticker = {t: isins.get(p) for p, t in listings.items()}
    tickers = sorted(isin_by_ticker)
    currencies = listing_currencies(tickers)
    results = Counter()
    for n, ticker in enumerate(tickers):
        try:
            result = refresh_history(ticker, full=full)
            if ticker not in currencies:
                refresh_info(ticker, isin=isin_by_ticker[ticker])
        except Exception as e:  # any download problem: retried at the next run
            logger.warning('Yahoo Finance download failed for %s: %s', ticker, e)
            result = 'failed'
        results[result] += 1
        logger.info('%5.1f%% (%d/%d) %s: %s', 100 * (n + 1) / len(tickers), n + 1, len(tickers), ticker, result)
        time.sleep(0.2)  # spread the requests to Yahoo Finance
    logger.info('ETF catalog history downloaded: %s', ', '.join(f'{k} {v}' for k, v in results.items()))


def average_traded_values(tickers: list[str]) -> pd.DataFrame:
    """Average daily traded value of the last 3 months (TRADED_VALUE_PERIOD before its last day) of each listing, in
    the currency of the listing: columns traded_value and currency by ticker. Listings without a price in the last
    MAX_DAYS_WITHOUT_PRICE days are left out.
    """
    recent = pd.Timestamp(date.today() - timedelta(days=MAX_DAYS_WITHOUT_PRICE))
    traded, currencies = traded_value(tickers, start=(recent - TRADED_VALUE_PERIOD).date())
    rows = {}
    for ticker in traded.columns:
        values = traded[ticker].dropna()
        if not values.empty and values.index[-1] >= recent:
            last_months = values[values.index > values.index[-1] - TRADED_VALUE_PERIOD]
            rows[ticker] = {'traded_value': last_months.mean(), 'currency': currencies[ticker]}
    return pd.DataFrame.from_dict(rows, orient='index', columns=['traded_value', 'currency'])


def rank_by_traded_value(account: Account, averages: pd.DataFrame) -> list[str]:
    """Tickers by decreasing average traded value; the values, in different currencies, are compared at the latest
    exchange rates, so the order is the same whatever the currency of the account.
    """
    foreign = sorted(set(averages['currency']) - {account.currency})
    fx_rates_df = load_fx_rates(account=account, curr_foreign_lst=foreign, index=pd.DatetimeIndex([]))
    latest = fx_rates_df.ffill().iloc[-1] if not fx_rates_df.empty else pd.Series(dtype=float)
    rates = averages['currency'].map(lambda c: 1.0 if c == account.currency else latest.get(f'{c}/{account.currency}'))
    return averages['traded_value'].mul(rates.astype(float)).dropna().sort_values(ascending=False).index.to_list()


def build_etf_catalog_data(account: Account) -> pd.DataFrame:
    """Build and save the ETF catalog of an account from the stored history: the MAX_ETF_CATALOG_SIZE ETFs with the
    highest average traded value and their performance metrics in the account currency (table etf_catalog, with an
    Excel copy in the results folder).
    """
    products = catalog_products()
    tickers = sorted(set(query_degiro_yahoo_map(products['id'].to_list()).values()))
    logger.info('%s: %d catalog products, %d distinct listings', account.name, len(products), len(tickers))
    averages = average_traded_values(tickers)
    logger.info('%s: traded value of %d listings with recent prices', account.name, len(averages))
    most_traded = rank_by_traded_value(account, averages)[:MAX_ETF_CATALOG_SIZE]
    prices = stored_adjusted_prices(account, most_traded)
    logger.info('%s: adjusted prices of the %d most traded in %s', account.name, prices.shape[1], account.currency)
    perf_df = compute_product_performance(prices)
    perf_df = perf_df.set_index(InstrPerfTableCols.ticker)
    logger.info('%s: metrics of %d listings', account.name, len(perf_df))
    catalog = pd.DataFrame({'ticker': [t for t in most_traded if t in perf_df.index]})
    catalog['rank'] = range(1, len(catalog) + 1)
    catalog['traded_value'] = averages.loc[catalog['ticker'], 'traded_value'].to_numpy()
    catalog['currency'] = averages.loc[catalog['ticker'], 'currency'].to_numpy()
    for metric, column in CATALOG_METRICS.items():
        catalog[column] = perf_df.loc[catalog['ticker'], metric.name].to_numpy()
    catalog['built_at'] = datetime.now()
    save_etf_catalog(account.name, catalog)
    save_df_to_excel(df=catalog_table(account), file_name=catalog_file_name(account), folder_name=RESULTS_DIR)
    return catalog


def catalog_file_name(account: Account) -> str:
    """Name of the Excel copy of the ETF catalog of an account."""
    return f'{account.name}_catalog'


def catalog_table(account: Account) -> pd.DataFrame:
    """ETF catalog of an account as shown: ticker, ISIN, name, traded value, currency and metrics (labels), by rank."""
    catalog = query_etf_catalog(account.name)
    info = query_yahoo_instruments(catalog['ticker'].to_list()).reindex(catalog['ticker'])
    table = pd.DataFrame(
        {
            InstrPerfTableCols.ticker: catalog['ticker'],
            InstrPerfTableCols.isin: info['isin'].to_numpy(),
            InstrPerfTableCols.name: info['name_long'].fillna(info['name_short']).to_numpy(),
            InstrPerfTableCols.volume: catalog['traded_value'],
            InstrPerfTableCols.currency: catalog['currency'],
        }
    )
    for metric, column in CATALOG_METRICS.items():
        table[metric.name] = catalog[column]
    return table
