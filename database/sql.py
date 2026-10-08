import logging

import pandas as pd
from degiro_connector.trading.models.product import ProductItem
from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.db_conn import SessionLocal, engine
from database.sql_utils import list_to_str, str_to_date
from database.table_definitions import (
    DegiroHistData,
    DegiroYahooMap,
    Product,
    YahooFinanceHistData,
    YahooFinanceHistDataPfInstr,
    YahooFinanceProdInfo,
)
from degiro.degiro_definitions import Exchanges, ProductTypes
from yahoo_finance.yahoo_finance import YF_PROD_INFO_LABEL, YFinHistCols, YFinInfoCols

logger = logging.getLogger(__name__)

YAHOO_FINANCE_DATA_OVERWRITE_DICT = {YFinHistCols.adj_close: True}
DEGIRO_HIST_COLS = ['open', 'high', 'low', 'close', 'price', 'volume']


def insert_product(product: ProductItem):
    data = Product(
        active=product.active,
        buy_order_types=list_to_str(product.buy_order_types),
        category=product.category,
        close_price=product.close_price,
        close_price_date=str_to_date(product.close_price_date),
        contract_size=product.contract_size,
        currency=product.currency,
        exchange_id=product.exchange_id,
        feed_quality=product.feed_quality,
        feed_quality_secondary=product.feed_quality_secondary,
        id=product.id,
        is_shortable=product.is_shortable,
        isin=product.isin,
        name=product.name,
        only_eod_prices=product.only_eod_prices,
        order_book_depth=product.order_book_depth,
        order_book_depth_secondary=product.order_book_depth_secondary,
        order_time_types=list_to_str(product.order_time_types),
        product_bit_types=list_to_str(product.product_bit_types),
        product_type=product.product_type,
        product_type_id=product.product_type_id,
        quality_switch_free=product.quality_switch_free,
        quality_switch_free_secondary=product.quality_switch_free_secondary,
        quality_switchable=product.quality_switchable,
        quality_switchable_secondary=product.quality_switchable_secondary,
        sell_order_types=list_to_str(product.sell_order_types),
        strike_price=product.strike_price,
        symbol=product.symbol,
        tradable=product.tradable,
        vwd_id=product.vwd_id,
        vwd_id_secondary=product.vwd_id_secondary,
        vwd_identifier_type=product.vwd_identifier_type,
        vwd_identifier_type_secondary=product.vwd_identifier_type_secondary,
        vwd_module_id=product.vwd_module_id,
        vwd_module_id_secondary=product.vwd_module_id_secondary,
    )
    with SessionLocal() as session:
        session.add(data)
        _commit(session, message=f'{product.id} - {product.name}')


def insert_degiro_hist(product_id: int, df: pd.DataFrame):
    # upsert historical data of one product; new non-null values overwrite stored ones,
    # while a field missing from this fetch keeps its stored value
    df = df.reindex(columns=DEGIRO_HIST_COLS).dropna(how='all')
    if df.empty:
        return
    rows = [
        {
            'product_id': int(product_id),
            'date': pd.Timestamp(ts).date(),
            **{col: (None if pd.isna(val) else float(val)) for col, val in row.items()},
        }
        for ts, row in df.iterrows()
    ]
    stmt = sqlite_insert(DegiroHistData)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DegiroHistData.product_id, DegiroHistData.date],
        set_={col: func.coalesce(stmt.excluded[col], DegiroHistData.__table__.c[col]) for col in DEGIRO_HIST_COLS},
    )
    with SessionLocal() as session:
        session.execute(stmt, rows)
        _commit(session, message=f'Degiro historical data of product {product_id} ({len(rows)} rows)')


def insert_yahoo_finance_data(data_dict: dict[YFinHistCols, pd.DataFrame], to_portfolio_instr_table: bool = True):
    if data_dict[YF_PROD_INFO_LABEL].empty:
        return
    with SessionLocal() as session:
        for col, df in data_dict.items():
            if df.empty:
                continue
            overwrite = YAHOO_FINANCE_DATA_OVERWRITE_DICT.get(col, False)
            if col == YF_PROD_INFO_LABEL:
                # write into product info table
                for ticker in df.columns:
                    cond = (
                        YahooFinanceProdInfo.ticker
                        == data_dict[YF_PROD_INFO_LABEL].loc[YFinInfoCols.symbol.value, ticker]
                    )
                    query = session.query(YahooFinanceProdInfo).where(cond)
                    if query.count() and not overwrite:
                        continue
                    query.delete()
                    session.flush()
                    data = []
                    for t in range(df.shape[0]):
                        data.append(
                            YahooFinanceProdInfo(
                                ticker=data_dict[YF_PROD_INFO_LABEL].loc[YFinInfoCols.symbol.value, ticker],
                                quote_type=df.index[t],
                                value=df[ticker].iloc[t],
                            )
                        )
                    session.add_all(data)
            else:
                # write into historical data table
                if to_portfolio_instr_table:
                    table = YahooFinanceHistDataPfInstr
                else:
                    table = YahooFinanceHistData
                for ticker in df.columns:
                    df_melt = pd.melt(df[ticker].reset_index(), id_vars='index', value_vars=ticker)
                    cond = table.ticker == data_dict[YF_PROD_INFO_LABEL].loc[YFinInfoCols.symbol.value, ticker]
                    cond = cond & (table.quote_type == str(col))
                    query = session.query(table).where(cond)
                    if query.count() and not overwrite:
                        continue
                    query.delete()
                    session.flush()
                    data = []
                    for t in range(df_melt.shape[0]):
                        data.append(
                            table(
                                ticker=data_dict[YF_PROD_INFO_LABEL].loc[YFinInfoCols.symbol.value, ticker],
                                date=df_melt['index'].iloc[t],
                                quote_type=str(col),
                                value=df_melt['value'].iloc[t],
                            )
                        )
                    session.add_all(data)
        _commit(session, message=f'Added Yahoo Finance data of product {data_dict[YF_PROD_INFO_LABEL]}.')


def replace_portfolio_instr_adj_close(ticker: str, ser: pd.Series):
    # adjusted prices are recomputed backwards at each dividend: replace the whole stored history of the ticker
    ser = ser.dropna()
    if ser.empty:
        return
    table = YahooFinanceHistDataPfInstr
    quote_type = str(YFinHistCols.adj_close)
    rows = [
        {'ticker': ticker, 'date': pd.Timestamp(ts).date(), 'quote_type': quote_type, 'value': float(val)}
        for ts, val in ser.items()
    ]
    with SessionLocal() as session:
        session.execute(delete(table).where((table.ticker == ticker) & (table.quote_type == quote_type)))
        session.execute(insert(table), rows)
        _commit(session, message=f'Yahoo Finance adjusted prices of {ticker} ({len(ser)} rows)')


def query_portfolio_instr_adj_close(tickers: list[str]) -> pd.DataFrame:
    # stored adjusted prices: date x ticker (exact ticker match)
    table = YahooFinanceHistDataPfInstr
    stmt = select(table.ticker, table.date, table.value).where(
        table.ticker.in_(tickers) & (table.quote_type == str(YFinHistCols.adj_close))
    )
    df = pd.read_sql(stmt, engine)
    if df.empty:
        return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'])
    return df.pivot(index='date', columns='ticker', values='value').sort_index()


def upsert_yahoo_finance_info(ticker: str, info: dict[str, str]):
    rows = [
        {'ticker': ticker, 'quote_type': field, 'value': str(value)}
        for field, value in info.items()
        if value is not None and not pd.isna(value)
    ]
    if not rows:
        return
    stmt = sqlite_insert(YahooFinanceProdInfo)
    stmt = stmt.on_conflict_do_update(
        index_elements=[YahooFinanceProdInfo.ticker, YahooFinanceProdInfo.quote_type],
        set_={'value': stmt.excluded.value},
    )
    with SessionLocal() as session:
        session.execute(stmt, rows)
        _commit(session)


def query_yahoo_finance_info_field(tickers: list[str], field: str) -> dict[str, str]:
    # one info field per ticker (exact ticker match)
    stmt = select(YahooFinanceProdInfo.ticker, YahooFinanceProdInfo.value).where(
        YahooFinanceProdInfo.ticker.in_(tickers) & (YahooFinanceProdInfo.quote_type == field)
    )
    with engine.connect() as connection:
        return dict(connection.execute(stmt).all())


def upsert_degiro_yahoo_map(product_ids: list[int], ticker: str):
    stmt = sqlite_insert(DegiroYahooMap)
    stmt = stmt.on_conflict_do_update(index_elements=[DegiroYahooMap.product_id], set_={'ticker': stmt.excluded.ticker})
    with SessionLocal() as session:
        session.execute(stmt, [{'product_id': int(p), 'ticker': ticker} for p in product_ids])
        _commit(session)


def query_degiro_yahoo_map(product_ids: list[int]) -> dict[int, str]:
    stmt = select(DegiroYahooMap.product_id, DegiroYahooMap.ticker).where(
        DegiroYahooMap.product_id.in_([int(p) for p in product_ids])
    )
    with engine.connect() as connection:
        return dict(connection.execute(stmt).all())


def _commit(session: Session, message: str | None = None):
    try:
        session.commit()
    except IntegrityError as e:
        # roll back the failed transaction, so that the session can still be used
        session.rollback()
        if 'UNIQUE constraint failed' in str(e.orig):
            if message is not None:
                logger.info('Entry %s already exists: skipped', message)
            return
        raise
    if message is not None:
        logger.debug('Added entry %s', message)


def query_products(
    product_name: str | None = None,
    product_id: int | None = None,
    product_isin: str | None = None,
    product_symbol: str | None = None,
    product_type: ProductTypes | None = None,
    tradable: bool | None = None,
    exchange: Exchanges | int | None = None,
) -> pd.DataFrame:
    query = select(Product)
    if isinstance(exchange, Exchanges):
        exchange = exchange.value
    if product_name is not None:
        query = query.where(Product.name == product_name)
    if product_id is not None:
        query = query.where(Product.id == product_id)
    if product_isin is not None:
        query = query.where(Product.isin == product_isin)
    if product_symbol is not None:
        query = query.where(Product.symbol == product_symbol)
    if product_type is not None:
        query = query.where(Product.product_type == product_type)
    if tradable is not None:
        query = query.where(Product.tradable == tradable)
    if exchange is not None:
        query = query.where(Product.exchange_id == exchange)
    df = pd.read_sql(query, engine)
    df = df.set_index('id', drop=False)
    return df


def query_degiro_hist(
    product_ids: int | list[int],
    columns: str | list[str] | None = None,
    date_start: pd.Timestamp | None = None,
    date_stop: pd.Timestamp | None = None,
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    # returns a date x product_id dataframe per requested field (a single dataframe if one field is requested)
    if isinstance(product_ids, int):
        product_ids = [product_ids]
    if columns is None:
        columns = DEGIRO_HIST_COLS
    elif isinstance(columns, str):
        columns = [columns]
    cond = DegiroHistData.product_id.in_(product_ids)
    if date_start is not None:
        cond = cond & (DegiroHistData.date >= pd.Timestamp(date_start).date())
    if date_stop is not None:
        cond = cond & (DegiroHistData.date < pd.Timestamp(date_stop).date())
    stmt = select(
        DegiroHistData.product_id, DegiroHistData.date, *[DegiroHistData.__table__.c[col] for col in columns]
    ).where(cond)
    df = pd.read_sql(stmt, engine)
    df['date'] = pd.to_datetime(df['date'])
    data = {col: df.pivot(columns='product_id', index='date', values=col).sort_index() for col in columns}
    if len(columns) == 1:
        return data[columns[0]]
    return data


def query_yahoo_finance_prod_info(
    isin: str | list[str] | None = None, ticker: str | list[str] | None = None
) -> pd.DataFrame:
    def _query_yahoo_finance_prod_info_single(isin: str = None, ticker: str = None) -> pd.DataFrame:
        if isin is not None:
            cond = (YahooFinanceProdInfo.quote_type == YFinInfoCols.isin.value) & (
                YahooFinanceProdInfo.value.ilike(f'{isin}%')
            )
            stmt = select(YahooFinanceProdInfo.ticker).where(cond)
            ticker_lst = pd.read_sql(stmt, engine)[YahooFinanceProdInfo.ticker.name].to_list()
            if ticker_lst:
                cond = []
                for t in ticker_lst:
                    cond.append(YahooFinanceProdInfo.ticker.ilike(f'{t}%'))
                cond = or_(*cond)
            else:
                cond = False
        elif ticker is not None:
            cond = YahooFinanceProdInfo.ticker.ilike(f'{ticker}%')
        else:
            cond = True
        stmt = select(YahooFinanceProdInfo).where(cond)
        df = pd.read_sql(stmt, engine)
        df = df.pivot(
            columns=YahooFinanceProdInfo.ticker.name,
            index=YahooFinanceProdInfo.quote_type.name,
            values=YahooFinanceProdInfo.value.name,
        )
        return df

    df = pd.DataFrame()
    if isinstance(isin, str):
        isin = [isin]
    elif isinstance(ticker, str):
        ticker = [ticker]
    if isin is not None:
        for entry in isin:
            df_single = _query_yahoo_finance_prod_info_single(isin=entry)
            df = pd.concat([df, df_single], axis=1)
    elif ticker is not None:
        for entry in ticker:
            df_single = _query_yahoo_finance_prod_info_single(ticker=entry)
            df = pd.concat([df, df_single], axis=1)
    else:
        df = _query_yahoo_finance_prod_info_single()
    return df


def search_yahoo_finance_instruments(text: str, limit: int = 20) -> pd.DataFrame:
    # instruments whose Yahoo ticker or ISIN starts with text or, from 3 characters, whose name contains it;
    # one row per ticker (ticker, name, isin, currency): ticker matches first, then ISIN, then name matches
    columns = ['ticker', 'name', 'isin', 'currency']
    text = text.strip()
    if not text:
        return pd.DataFrame(columns=columns)
    pattern = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')  # typed text taken literally
    info = YahooFinanceProdInfo
    names = [YFinInfoCols.name_long.value, YFinInfoCols.name_short.value]
    conditions = [
        info.ticker.ilike(f'{pattern}%', escape='\\'),
        (info.quote_type == YFinInfoCols.isin.value) & info.value.ilike(f'{pattern}%', escape='\\'),
    ]
    if len(text) >= 3:
        conditions.append(info.quote_type.in_(names) & info.value.ilike(f'%{pattern}%', escape='\\'))
    tickers = []
    for cond in conditions:
        stmt = select(info.ticker).where(cond).distinct().order_by(info.ticker).limit(limit)
        tickers += [t for t in pd.read_sql(stmt, engine)[info.ticker.name] if t not in tickers]
    tickers = tickers[:limit]
    if not tickers:
        return pd.DataFrame(columns=columns)
    stmt = select(info.ticker, info.quote_type, info.value).where(
        info.ticker.in_(tickers) & info.quote_type.in_(names + [YFinInfoCols.isin.value, YFinInfoCols.currency.value])
    )
    df = pd.read_sql(stmt, engine).pivot_table(
        index=info.ticker.name, columns=info.quote_type.name, values=info.value.name, aggfunc='first'
    )
    df = df.reindex(index=tickers, columns=names + [YFinInfoCols.isin.value, YFinInfoCols.currency.value])
    df['name'] = df[names[0]].where(df[names[0]].notna() & (df[names[0]] != ''), df[names[1]])
    return (
        df.rename(columns={YFinInfoCols.isin.value: 'isin', YFinInfoCols.currency.value: 'currency'})
        .reset_index()
        .rename(columns={info.ticker.name: 'ticker'})[columns]
    )


def query_yahoo_finance_hist_data(
    tickers: str | list[str] | None = None,
    isin: str | None = None,
    columns: str | list[str] | YFinHistCols | list[YFinHistCols] | None = None,
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    if isinstance(tickers, list):  # list of tickers is only possible in case of Yahoo tickers
        ticker_lst = tickers
    else:
        info_df = query_yahoo_finance_prod_info(isin=isin, ticker=tickers)
        if info_df.empty:
            return {}
        ticker_lst = info_df.loc[YFinInfoCols.symbol.value, :].to_list()
    if columns is None:
        columns = [col for col in YFinHistCols]
    elif isinstance(columns, str) or isinstance(columns, YFinHistCols):
        columns = [columns]
    data = {}
    for col in columns:
        cond = YahooFinanceHistData.quote_type == str(col)
        cond = cond & (YahooFinanceHistData.ticker.in_(ticker_lst))
        stmt = select(YahooFinanceHistData).where(cond)
        df = pd.read_sql(stmt, engine)
        if df.empty:
            continue
        df = df.pivot(
            columns=YahooFinanceHistData.ticker.name,
            index=YahooFinanceHistData.date.name,
            values=YahooFinanceHistData.value.name,
        )
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        data[col] = df
    if len(columns) == 1:
        return df
    else:
        return data


def get_max_product_id() -> int:
    with SessionLocal() as session:
        return session.query(func.max(Product.id)).all()[0][0]
