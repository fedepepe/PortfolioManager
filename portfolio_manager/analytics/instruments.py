"""Instrument data from Yahoo Finance: prices in the account currency, performance and the ETF catalog of each
account.
"""

import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd
from degiro_connector.quotecast.models.chart import Interval
from degiro_connector.quotecast.tools.chart_fetcher import ChartFetcher
from degiro_connector.trading.api import API

from portfolio_manager.analytics.metrics import Metrics, PerfDataTabs, compute_portfolio_metrics
from portfolio_manager.config.accounts import Account, list_accounts
from portfolio_manager.config.settings import RESULTS_DIR
from portfolio_manager.degiro.charts import ChartType, DegiroChartError, exchange_rate, fetch_chart, load_fx_rates
from portfolio_manager.degiro.connection import get_degiro_connection
from portfolio_manager.degiro.definitions import Exchanges, ProductTypes, exchange_name, yahoo_suffix
from portfolio_manager.degiro.products import load_portfolio_products
from portfolio_manager.market_data.history import (
    Listing,
    ListingSearches,
    PriceReference,
    find_listing,
    is_missing,
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
    delete_currency_fix,
    delete_unused_currency_fixes,
    query_account_product_ids,
    query_currency_fixes,
    query_currency_pairs,
    query_degiro_hist,
    query_degiro_yahoo_map,
    query_etf_catalog,
    query_listing_isins,
    query_listing_searches,
    query_products,
    query_yahoo_instruments,
    save_currency_fix,
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
REMAP_PERIOD = pd.DateOffset(months=3)  # a catalog product whose listing was found is checked again after this
OWN_LINE_RULES = ('own line', 'own line (confirmed)', 'own line (currency fixed)')
# a product whose last Degiro price is older no longer trades on its exchange: no listing searched
MAX_DEGIRO_PRICE_AGE = timedelta(days=10)
NO_REFERENCE_RULES = ('no Degiro prices', 'no recent Degiro prices')  # products not searched: nothing to compare
PRICE_CHECK_PERIOD = pd.DateOffset(months=3)  # last 3 months of Degiro prices compared to those of the listings
LISTING_REVIEW_FILE = 'yahoo_mapping'  # Excel copy of the mapping of the Degiro products, for review
# Yahoo information fields (rows of the information tables of the catalogs) and their columns in the database
INFO_COLUMNS = {
    YFinInfoCols.currency.value: 'currency',
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
    """Information of Yahoo Finance listings: Yahoo field x ticker (the layout of the catalog tables), with the Degiro
    ISIN of the products mapped to each listing.
    """
    df = query_yahoo_instruments(tickers).reindex(tickers)
    info = pd.DataFrame({field: df[column] for field, column in INFO_COLUMNS.items()}).T
    info.loc[YFinInfoCols.symbol.value] = tickers
    isins = query_listing_isins(tickers)
    info.loc[YFinInfoCols.isin.value] = [isins.get(t) for t in tickers]
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
    info = instruments_info([listing])
    info.loc[YFinInfoCols.isin.value] = isin  # the ISIN searched (the listing may not be mapped to a Degiro product)
    df = compute_product_performance(adj_close_df=load_adjusted_closes([listing]), prod_info_df=info)
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
    """Tradable ETFs of the Degiro catalog on the exchanges of the catalog: every Degiro product (a line of a fund on
    one exchange, in one currency), also when the fund trades under the same symbol elsewhere.
    """
    etf_info_df = query_products(product_type=ProductTypes.ETF, tradable=True)
    return etf_info_df[etf_info_df[Product.exchange_id.name].isin([x.value for x in CATALOG_EXCHANGES])]


def traded_product_ids() -> set[int]:
    """Products traded in any account."""
    return {p for account in list_accounts() for p in query_account_product_ids(account.name)}


class DegiroPrices:
    """Degiro prices of products, to which the prices of their candidate listings are compared: the last 3 months
    (PRICE_CHECK_PERIOD) of the stored Degiro history (the products traded in an account), or of a chart downloaded
    from Degiro when a connection is given.
    """

    def __init__(self, degiro_conn: API | None = None):
        self.chart_fetcher = None
        self.missing_reason: str | None = None  # why the last product had no reference prices
        if degiro_conn is not None:
            self.chart_fetcher = ChartFetcher(user_token=degiro_conn.get_client_details()['data']['id'])
        pairs = query_currency_pairs()
        pair_prices = query_degiro_hist(sorted(pairs.values()), columns=ChartType.PRICE.value) if pairs else None
        self.pair_prices = pd.DataFrame() if pair_prices is None else pair_prices
        self.pair_prices = self.pair_prices.rename(columns={product_id: pair for pair, product_id in pairs.items()})

    def reference(self, product) -> PriceReference | None:
        """Last 3 months of Degiro prices of a product (a row of the products table), None if Degiro has none (why in
        missing_reason).
        """
        self.missing_reason = None
        stored = query_degiro_hist(int(product.id), columns=ChartType.PRICE.value)
        prices = stored[int(product.id)].dropna() if int(product.id) in stored else pd.Series(dtype=float)
        if prices.empty and self.chart_fetcher is not None:
            if not self._has_chart(product):
                self.missing_reason = 'no Degiro chart id'
                return None
            try:
                chart = fetch_chart(self.chart_fetcher, int(product.vwd_id), period=Interval.P3M)
            except DegiroChartError as e:
                self.missing_reason = f'Degiro chart refused: {e}'
                return None
            prices = chart[ChartType.PRICE.value].dropna() if ChartType.PRICE.value in chart else prices
        if prices.empty:
            self.missing_reason = 'Degiro chart empty' if self.chart_fetcher is not None else 'no stored Degiro prices'
            return None
        prices = prices[prices.index > prices.index[-1] - PRICE_CHECK_PERIOD]
        currency, factor = major_currency(product.currency)
        return PriceReference(
            prices=prices * factor,
            currency=currency,
            rate=lambda other: exchange_rate(self.pair_prices, currency=other, base=currency),
        )

    @staticmethod
    def _has_chart(product) -> bool:
        # Degiro charts are requested by the vwd id of the product
        return not is_missing(getattr(product, 'vwd_id', None)) and product.vwd_identifier_type == 'issueid'


def search_product_listing(
    product, searches: ListingSearches | None = None, degiro_prices: DegiroPrices | None = None
) -> Listing:
    """Find the Yahoo Finance listing of a Degiro product (a row of the products table): its own line first, checked on
    the prices of the product (see find_listing); without Degiro prices, the product is not found. A failed search is a
    result too.
    """
    try:
        reference = degiro_prices.reference(product) if degiro_prices is not None else None
        if degiro_prices is not None and reference is None:
            return Listing(None, 'not found', 'no Degiro prices', degiro_prices.missing_reason)
        if reference is not None and reference.prices.index[-1] < pd.Timestamp(date.today() - MAX_DEGIRO_PRICE_AGE):
            end = reference.prices.index[-1].date()
            return Listing(None, 'not found', 'no recent Degiro prices', f'Degiro prices to {end}')
        listing = find_listing(
            search=product.isin,
            ticker=product.symbol,
            name=product.name,
            exchange=yahoo_suffix(product.exchange_id),
            currency=major_currency(product.currency)[0],
            reference=reference,
            searches=searches,
        )
        return listing
    except Exception as e:  # any download problem: searched again at the next run
        return Listing(None, 'failed', detail=f'{type(e).__name__}: {e}')


def save_listing(product_id: int, listing: Listing):
    """Save the result of the search of the listing of a product, and the currency fix of the listing found: saved if
    its prices are in another currency than Yahoo reports, removed if they match in the reported one.
    """
    save_listing_search([product_id], listing.ticker, listing.status, listing.rule)
    if listing.ticker is None or listing.rule in NO_REFERENCE_RULES:
        return
    if listing.currency_fix is not None:
        fix = listing.currency_fix
        save_currency_fix(listing.ticker, fix.yahoo_currency, fix.currency, fix.gap, product_id)
    else:
        delete_currency_fix(listing.ticker)


def products_to_search(
    products: pd.DataFrame,
    remap: bool = False,
    retry_not_found: bool = False,
    remap_before: datetime | None = None,
    remap_found_not_own_line: bool = False,
    remap_after: pd.DateOffset | None = None,
) -> pd.DataFrame:
    """The products whose listing must be searched: never searched, failed, not found PRODUCT_SEARCH_RETRY ago (or
    with retry_not_found), found only with remap, with remap_before if searched before then (to resume a remap), with
    remap_found_not_own_line if not on their own line, or with remap_after if searched longer ago than that.
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
            return (
                retry_not_found or pd.isna(search['searched_at']) or now - search['searched_at'] >= PRODUCT_SEARCH_RETRY
            )
        if remap_after is not None and (pd.isna(search['searched_at']) or search['searched_at'] < now - remap_after):
            return True
        if remap_before is not None:
            return pd.isna(search['searched_at']) or search['searched_at'] < pd.Timestamp(remap_before)
        if remap_found_not_own_line:
            return search['rule'] not in OWN_LINE_RULES
        return remap

    return products[products['id'].map(due)]


def map_listings(
    products: pd.DataFrame,
    degiro_prices: DegiroPrices,
    remap: bool = False,
    retry_not_found: bool = False,
    remap_before: datetime | None = None,
    remap_found_not_own_line: bool = False,
) -> Counter:
    """Search and save the Yahoo Finance listing of the products to search (see products_to_search), checked on their
    Degiro prices; a failed search keeps the listing found before. Returns the count of each status.
    """
    todo = products_to_search(
        products,
        remap=remap,
        retry_not_found=retry_not_found,
        remap_before=remap_before,
        remap_found_not_own_line=remap_found_not_own_line,
        remap_after=REMAP_PERIOD,
    )
    found_before = query_degiro_yahoo_map(todo['id'].to_list())
    searches, results = ListingSearches(), Counter()
    for n, product in enumerate(todo.itertuples()):
        listing = search_product_listing(product, searches, degiro_prices)
        if listing.status != 'failed' or product.id not in found_before:
            save_listing(product.id, listing)
        if listing.rule == 'no Degiro prices' and (listing.detail or '').startswith('Degiro chart refused'):
            results['Degiro chart refused'] += 1
        else:
            results[listing.rule if listing.rule in NO_REFERENCE_RULES else listing.status] += 1
        logger.info(
            '%5.1f%% (%d/%d) %s %s %s %s %s: %s%s',
            100 * (n + 1) / len(todo),
            n + 1,
            len(todo),
            product.id,
            exchange_name(product.exchange_id),
            product.isin,
            product.symbol,
            listing.ticker or '',
            listing.rule or listing.status,
            f' ({listing.detail})' if listing.detail else '',
        )
    logger.info('Listings searched: %s', ', '.join(f'{k} {v}' for k, v in results.items()) or 'none due')
    removed = delete_unused_currency_fixes()
    if removed:
        logger.info('Currency fixes of listings no longer mapped removed: %d', removed)
    return results


def map_etf_catalog(
    remap: bool = False,
    retry_not_found: bool = False,
    remap_before: datetime | None = None,
    remap_found_not_own_line: bool = False,
    degiro_prices: DegiroPrices | None = None,
) -> Counter:
    """Search the Yahoo Finance listing of the ETFs of the catalog (see map_listings; the Degiro prices are downloaded,
    which needs a Degiro connection), then save the Excel copy of the mapping for review. The products traded in an
    account keep their listing: see remap_portfolio_listings.
    """
    products = catalog_products()
    products = products[~products['id'].isin(traded_product_ids())]
    if degiro_prices is None:
        degiro_prices = DegiroPrices(get_degiro_connection())
    results = map_listings(
        products,
        degiro_prices,
        remap=remap,
        retry_not_found=retry_not_found,
        remap_before=remap_before,
        remap_found_not_own_line=remap_found_not_own_line,
    )
    export_listing_review()
    return results


def remap_portfolio_listings(apply: bool = False, degiro_prices: DegiroPrices | None = None) -> pd.DataFrame:
    """Search again the listing of the products traded in the accounts, checked on their stored Degiro prices: the
    products whose listing or currency fix would change (product_id, symbol, name, stored, found, rule, currency: the
    currency of the prices if Yahoo reports another one); with apply, they are saved and the new listings downloaded.
    A product without Degiro prices keeps its listing.
    """
    products = query_products(product_id=sorted(traded_product_ids()))
    stored = query_degiro_yahoo_map(products['id'].to_list())
    fixed = query_currency_fixes()['currency'].to_dict()
    degiro_prices = degiro_prices or DegiroPrices()
    searches, changes = ListingSearches(), []
    for product in products.itertuples():
        listing = search_product_listing(product, searches, degiro_prices)
        if listing.status == 'failed' or listing.rule in NO_REFERENCE_RULES:
            continue
        currency = listing.currency_fix.currency if listing.currency_fix is not None else None
        before = stored.get(product.id)
        if listing.ticker == before and currency == fixed.get(before):
            continue
        changes.append((product.id, product.symbol, product.name, before, listing.ticker, listing.rule, currency))
        if apply:
            save_listing(product.id, listing)
            if listing.ticker is not None and listing.ticker != before:
                store_listing(listing.ticker)
    if apply:
        delete_unused_currency_fixes()
    columns = ['product_id', 'symbol', 'name', 'stored', 'found', 'rule', 'currency']
    return pd.DataFrame(changes, columns=columns)


def export_listing_review():
    """Excel copy of the mapping of the Degiro products to Yahoo Finance listings, for review (results folder)."""
    searches = query_listing_searches()
    products = query_products(product_id=searches['product_id'].to_list()).reset_index(drop=True)
    instruments = query_yahoo_instruments(sorted(searches['ticker'].dropna().unique()))
    review = searches.merge(
        products[['id', 'exchange_id', 'symbol', 'name']], left_on='product_id', right_on='id', how='left'
    )
    review['exchange'] = review['exchange_id'].map(exchange_name)
    review['yahoo_name'] = review['ticker'].map(instruments['name_long'].fillna(instruments['name_short']))
    review['yahoo_currency'] = review['ticker'].map(instruments['currency'])
    fixes = query_currency_fixes()
    review['currency_fixed'] = review['ticker'].map(fixes['currency'])  # the currency of the prices, if not Yahoo's
    columns = ['product_id', 'exchange', 'symbol', 'isin', 'name', 'ticker', 'yahoo_name', 'yahoo_currency']
    review = review[columns + ['currency_fixed', 'status', 'rule', 'searched_at']].sort_values(['status', 'isin'])
    sheets = {'Mapping': review.set_index('product_id'), 'Currency fixes': fixes}
    save_df_dict_to_excel(df_dict=sheets, file_name=LISTING_REVIEW_FILE, folder_name=RESULTS_DIR)


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
                refresh_info(ticker)
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


def traded_values_in_account_currency(account: Account, averages: pd.DataFrame) -> pd.Series:
    """Average traded values (see average_traded_values) in the account currency, at the latest exchange rates
    (listings in a currency without exchange rate are left out).
    """
    foreign = sorted(set(averages['currency']) - {account.currency})
    fx_rates_df = load_fx_rates(account=account, curr_foreign_lst=foreign, index=pd.DatetimeIndex([]))
    latest = fx_rates_df.ffill().iloc[-1] if not fx_rates_df.empty else pd.Series(dtype=float)
    rates = averages['currency'].map(lambda c: 1.0 if c == account.currency else latest.get(f'{c}/{account.currency}'))
    return averages['traded_value'].mul(rates.astype(float)).dropna()


def rank_by_traded_value(account: Account, averages: pd.DataFrame) -> list[str]:
    """Tickers by decreasing average traded value; the values, in different currencies, are compared at the latest
    exchange rates, so the order is the same whatever the currency of the account.
    """
    return traded_values_in_account_currency(account, averages).sort_values(ascending=False).index.to_list()


def one_listing_per_fund(account: Account, ranked: list[str], currencies: dict[str, str]) -> list[str]:
    """One listing per fund (Degiro ISIN) among listings ranked by traded value: the most traded one in the account
    currency, otherwise the most traded one; the funds in the order of their most traded listing.
    """
    isins = query_listing_isins(ranked)
    chosen: dict[str, str] = {}
    for ticker in ranked:
        fund = isins.get(ticker, ticker)  # a listing without ISIN is a fund of its own
        best = chosen.get(fund)
        if best is None or (currencies.get(best) != account.currency and currencies.get(ticker) == account.currency):
            chosen[fund] = ticker
    return list(chosen.values())


def build_etf_catalog_data(account: Account) -> pd.DataFrame:
    """Build and save the ETF catalog of an account from the stored history: the MAX_ETF_CATALOG_SIZE funds with the
    highest average traded value, each with one listing (see one_listing_per_fund), and their performance metrics in
    the account currency (table etf_catalog, with an Excel copy in the results folder).
    """
    products = catalog_products()
    tickers = sorted(set(query_degiro_yahoo_map(products['id'].to_list()).values()))
    logger.info('%s: %d catalog products, %d distinct listings', account.name, len(products), len(tickers))
    averages = average_traded_values(tickers)
    logger.info('%s: traded value of %d listings with recent prices', account.name, len(averages))
    ranked = rank_by_traded_value(account, averages)
    funds = one_listing_per_fund(account, ranked, averages['currency'].to_dict())
    logger.info('%s: %d funds among %d listings', account.name, len(funds), len(ranked))
    most_traded = funds[:MAX_ETF_CATALOG_SIZE]
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
    isins = query_listing_isins(catalog['ticker'].to_list())  # Degiro ISIN of the fund
    table = pd.DataFrame(
        {
            InstrPerfTableCols.ticker: catalog['ticker'],
            InstrPerfTableCols.isin: catalog['ticker'].map(isins).to_numpy(),
            InstrPerfTableCols.name: info['name_long'].fillna(info['name_short']).to_numpy(),
            InstrPerfTableCols.volume: catalog['traded_value'],
            InstrPerfTableCols.currency: catalog['currency'],
        }
    )
    for metric, column in CATALOG_METRICS.items():
        table[metric.name] = catalog[column]
    return table
