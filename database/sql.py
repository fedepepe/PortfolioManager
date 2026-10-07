from typing import Optional, Dict, List

import pandas as pd
from degiro_connector.trading.models.product import ProductItem
from sqlalchemy import or_
from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from database.db_conn import engine, conn
from database.sql_utils import list_to_str, str_to_date
from database.table_definitions import Product, DegiroHistData, YahooFinanceHistData, YahooFinanceProdInfo
from database.table_definitions import YahooFinanceHistDataPfInstr
from degiro.degiro_definitions import ProductTypes, Exchanges
from yahoo_finance.yahoo_finance import YF_PROD_INFO_LABEL, YFinInfoCols, YFinHistCols

YAHOO_FINANCE_DATA_OVERWRITE_DICT = {YFinHistCols.adj_close: True}
DEGIRO_HIST_COLS = ['open', 'high', 'low', 'close', 'price', 'volume']


def insert_product(product: ProductItem):
    data = Product(active=product.active,
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
    conn.add(data)
    print([k for k, v in product.dict().items() if isinstance(v, list)])
    db_commit(message=f'{product.id} - {product.name}')


def insert_degiro_hist(product_id: int, df: pd.DataFrame):
    # upsert historical data of one product; new non-null values overwrite stored ones,
    # while a field missing from this fetch keeps its stored value
    df = df.reindex(columns=DEGIRO_HIST_COLS).dropna(how='all')
    if df.empty:
        return
    rows = [{'product_id': int(product_id),
             'date': pd.Timestamp(ts).date(),
             **{col: (None if pd.isna(val) else float(val)) for col, val in row.items()}}
            for ts, row in df.iterrows()]
    stmt = sqlite_insert(DegiroHistData)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DegiroHistData.product_id, DegiroHistData.date],
        set_={col: func.coalesce(stmt.excluded[col], DegiroHistData.__table__.c[col]) for col in DEGIRO_HIST_COLS})
    conn.execute(stmt, rows)
    db_commit(message=f'Degiro historical data of product {product_id} ({len(rows)} rows)')


def insert_yahoo_finance_data(data_dict: Dict[YFinHistCols, pd.DataFrame],
                              to_portfolio_instr_table: bool = True):
    if data_dict[YF_PROD_INFO_LABEL].empty:
        return
    for col, df in data_dict.items():
        if df.empty:
            continue
        overwrite = YAHOO_FINANCE_DATA_OVERWRITE_DICT.get(col, False)
        if col == YF_PROD_INFO_LABEL:
            # write into product info table
            for ticker in df.columns:
                cond = YahooFinanceProdInfo.ticker == data_dict[YF_PROD_INFO_LABEL].loc[
                    YFinInfoCols.symbol.value, ticker]
                query = conn.query(YahooFinanceProdInfo).where(cond)
                if query.count() and not overwrite:
                    continue
                query.delete()
                conn.flush()
                data = []
                for t in range(df.shape[0]):
                    data.append(YahooFinanceProdInfo(
                        ticker=data_dict[YF_PROD_INFO_LABEL].loc[YFinInfoCols.symbol.value, ticker],
                        quote_type=df.index[t],
                        value=df[ticker].iloc[t]))
                conn.add_all(data)
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
                query = conn.query(table).where(cond)
                if query.count() and not overwrite:
                    continue
                query.delete()
                conn.flush()
                data = []
                for t in range(df_melt.shape[0]):
                    data.append(table(
                        ticker=data_dict[YF_PROD_INFO_LABEL].loc[YFinInfoCols.symbol.value, ticker],
                        date=df_melt['index'].iloc[t],
                        quote_type=str(col),
                        value=df_melt['value'].iloc[t]))
                conn.add_all(data)
    db_commit(message=f'Added Yahoo Finance data of product {data_dict[YF_PROD_INFO_LABEL]}.')


def db_commit(message: Optional[str] = None):
    try:
        conn.commit()
    except IntegrityError as e:
        # always roll back, otherwise the session stays in a failed state and the next commit is lost
        conn.rollback()
        if 'UNIQUE constraint failed' in str(e.orig):
            if message is not None:
                print(f'Entry {message} already exists. Skipped.')
            return
        raise
    if message is not None:
        print(f'Added entry {message}.')


def query_products(product_name: Optional[str] = None,
                   product_id: Optional[int] = None,
                   product_isin: Optional[str] = None,
                   product_symbol: Optional[str] = None,
                   product_type: Optional[ProductTypes] = None,
                   tradable: Optional[bool] = None,
                   exchange: Optional[Exchanges | int] = None,
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


def query_tradable_products(product_type: ProductTypes) -> pd.DataFrame:
    etf_info_df = pd.DataFrame()
    for exc in Exchanges:
        df = query_products(product_type=product_type, tradable=True, exchange=exc.value)
        etf_info_df = pd.concat([etf_info_df, df])
    return etf_info_df


def query_degiro_hist(product_ids: int | List[int],
                      columns: Optional[str | List[str]] = None,
                      date_start: Optional[pd.Timestamp] = None,
                      date_stop: Optional[pd.Timestamp] = None
                      ) -> pd.DataFrame | Dict[str, pd.DataFrame]:
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
    stmt = select(DegiroHistData.product_id, DegiroHistData.date,
                  *[DegiroHistData.__table__.c[col] for col in columns]).where(cond)
    df = pd.read_sql(stmt, engine)
    df['date'] = pd.to_datetime(df['date'])
    data = {col: df.pivot(columns='product_id', index='date', values=col).sort_index() for col in columns}
    if len(columns) == 1:
        return data[columns[0]]
    return data


def query_yahoo_finance_prod_info(isin: Optional[str | List[str]] = None,
                                  ticker: Optional[str | List[str]] = None
                                  ) -> pd.DataFrame:
    def _query_yahoo_finance_prod_info_single(isin: str = None,
                                              ticker: str = None
                                              ) -> pd.DataFrame:
        if isin is not None:
            cond = ((YahooFinanceProdInfo.quote_type == YFinInfoCols.isin.value)
                    & (YahooFinanceProdInfo.value.ilike(f'{isin}%')))
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
        df = df.pivot(columns=YahooFinanceProdInfo.ticker.name,
                      index=YahooFinanceProdInfo.quote_type.name,
                      values=YahooFinanceProdInfo.value.name)
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


def query_yahoo_finance_hist_data(tickers: Optional[str | List[str]] = None,
                                  isin: Optional[str] = None,
                                  columns: Optional[str | List[str] | YFinHistCols | List[YFinHistCols]] = None,
                                  ) -> pd.DataFrame | Dict[str, pd.DataFrame]:
    if isinstance(tickers, List):  # list of tickers is only possible in case of Yahoo tickers
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
        df = df.pivot(columns=YahooFinanceHistData.ticker.name,
                      index=YahooFinanceHistData.date.name,
                      values=YahooFinanceHistData.value.name)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        data[col] = df
    if len(columns) == 1:
        return df
    else:
        return data


def get_product_types() -> float:
    return conn.query(Product.product_type).distinct().all()


def get_max_product_id() -> int:
    return conn.query(func.max(Product.id)).all()[0][0]


if __name__ == '__main__':
    # df = query_yahoo_finance_hist_data(column=YFinHistCols.adj_close, ticker='EXSI.DE')  # , isin='IE00BNKF6C99')
    df = query_yahoo_finance_prod_info()
    # df = query_products(product_type=ProductTypes.ETF, tradable=True)
    # df = query_products(product_type=ProductTypes.ETF,
    #                     tradable=True
    #                     )[[Product.isin.name,
    #                        Product.symbol.name,
    #                        Product.name.name
    #                        ]]
    print(df)
