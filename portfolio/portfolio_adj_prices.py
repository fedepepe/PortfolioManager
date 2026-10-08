# Adjusted prices of the portfolio instruments, full history, in the account currency, labelled by Degiro product id.
# Online, the prices are downloaded from Yahoo Finance and stored in the database; offline (or if a download fails),
# the prices stored in the database are used.
import logging
from typing import NamedTuple, Optional, Tuple, Dict, List

import pandas as pd

from config.accounts import Accounts
from database.sql import replace_portfolio_instr_adj_close, query_portfolio_instr_adj_close
from database.sql import upsert_yahoo_finance_info, query_yahoo_finance_info_field
from database.sql import upsert_degiro_yahoo_map, query_degiro_yahoo_map
from degiro.products import load_portfolio_products
from portfolio.instruments_performance import fetch_instr_hist_data, prices_to_base_curr, is_missing
from yahoo_finance.yahoo_finance import YFinHistCols, YFinInfoCols, YF_PROD_INFO_LABEL, is_yahoo_reachable


class AdjPrices(NamedTuple):
    prices: pd.DataFrame  # date x product id, account currency; products without adjusted prices are missing
    summary: str  # where the prices come from, for logs and the dashboard


def _fetch_from_yahoo(
    isin: Optional[str], symbol: Optional[str], name: Optional[str]
) -> Optional[Tuple[str, pd.Series, Dict]]:
    # (Yahoo ticker, adjusted prices in the instrument currency, instrument info), None if no data is found
    data = fetch_instr_hist_data(isin_lst=[isin], columns=YFinHistCols.adj_close, ticker_lst=[symbol], name_lst=[name])
    prices_df = data[YFinHistCols.adj_close].dropna(axis=1, how='all')
    if prices_df.empty:
        return None
    ticker = prices_df.columns[0]
    return ticker, prices_df[ticker].dropna(), data[YF_PROD_INFO_LABEL][ticker].to_dict()


def get_portfolio_adj_prices(account: Accounts) -> AdjPrices:
    products_df = load_portfolio_products(account=account)
    online = is_yahoo_reachable()
    stored_map = query_degiro_yahoo_map(products_df['id'].to_list())
    # products with the same ISIN (e.g. one ETF on two exchanges) share one Yahoo Finance listing
    groups = products_df.groupby(products_df['isin'].fillna(products_df['id'].astype(str)), sort=False)
    ticker_by_product: Dict[int, str] = {}
    downloaded, from_db, missing = [], [], []
    for _, group in groups:
        product_ids = group['id'].astype(int).to_list()
        first = group.iloc[0]
        result = None
        if online:
            try:
                result = _fetch_from_yahoo(
                    isin=None if is_missing(first['isin']) else first['isin'],
                    symbol=None if is_missing(first['symbol']) else first['symbol'],
                    name=None if is_missing(first['name']) else first['name'],
                )
            except Exception as e:  # any download problem: fall back to the stored prices
                logging.warning(f'Yahoo Finance download failed for {first["name"]}: {e}')
        if result is not None:
            ticker, prices, info = result
            replace_portfolio_instr_adj_close(ticker=ticker, ser=prices)
            upsert_yahoo_finance_info(ticker=ticker, info=info)
            upsert_degiro_yahoo_map(product_ids=product_ids, ticker=ticker)
            downloaded.append(ticker)
        else:
            ticker = next((stored_map[p] for p in product_ids if p in stored_map), None)
            if ticker is None:
                missing.extend(product_ids)
                continue
            from_db.append(ticker)
        ticker_by_product.update({p: ticker for p in product_ids})

    # read back from the database, so that online and offline runs use exactly the same data
    tickers = sorted(set(ticker_by_product.values()))
    raw_df = query_portfolio_instr_adj_close(tickers) if tickers else pd.DataFrame()
    currencies = query_yahoo_finance_info_field(tickers, YFinInfoCols.currency.value) if tickers else {}
    tickers = [t for t in tickers if t in raw_df.columns and t in currencies]
    missing.extend([p for p, t in ticker_by_product.items() if t not in tickers])
    prices = pd.DataFrame()
    if tickers:
        base_df = prices_to_base_curr(
            account=account, price_df=raw_df[tickers], curr_info=[currencies[t] for t in tickers]
        )
        prices = pd.DataFrame({p: base_df[t] for p, t in ticker_by_product.items() if t in base_df.columns})

    parts = [f'{len(downloaded)} downloaded from Yahoo Finance'] if online else []
    if from_db:
        stored = [t for t in set(from_db) if t in raw_df.columns]
        last = min(raw_df[t].last_valid_index() for t in stored) if stored else None
        parts.append(f'{len(set(from_db))} from database' + (f' (until {last:%Y-%m-%d})' if last is not None else ''))
    if missing:
        parts.append(f'{len(set(missing))} with unadjusted Degiro prices')
    summary = f'{"online" if online else "offline"}: {", ".join(parts)}'
    logging.info(f'{account.name} adjusted prices: {summary}')
    return AdjPrices(prices=prices, summary=summary)
