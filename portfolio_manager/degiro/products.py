"""DeGiro product information: full catalog in the database, portfolio products in Excel."""

import pandas as pd
from degiro_connector.trading.api import API

from portfolio_manager.config.accounts import Accounts
from portfolio_manager.config.settings import DATA_DIR
from portfolio_manager.degiro.connection import get_degiro_connection
from portfolio_manager.degiro.definitions import ProductTypes
from portfolio_manager.degiro.transactions import load_tx_history
from portfolio_manager.storage.files import load_df_from_excel, save_df_to_excel
from portfolio_manager.storage.models import Product
from portfolio_manager.storage.queries import insert_product


def fetch_full_product_catalog(degiro_conn: API | None = None):
    """Download the information of every DeGiro product into the database (long)."""
    if degiro_conn is None:
        degiro_conn = get_degiro_connection()
    # FETCH PRODUCT INFO
    n_start = 0  # get_max_product_id()
    if n_start is None:
        n_start = 0
    n = n_start
    while True:
        product_info = degiro_conn.get_products_info(
            product_list=[i for i in range(n, n + 1000)],
            raw=False,
        )
        if hasattr(product_info, 'data'):
            for prod_id in product_info.data:
                if (
                    product_info.data[prod_id].product_type
                    in [ProductTypes.CURRENCY, ProductTypes.STOCK, ProductTypes.ETF, ProductTypes.BOND]
                    and product_info.data[prod_id].active is True
                ):
                    insert_product(product_info.data[prod_id])
        if n > n_start + 40e6:
            break
        n += 1000
    return product_info


def fetch_product_info(degiro_conn: API | None = None, product_ids: int | list[int] = 11853206) -> pd.DataFrame:
    """Information of the given products from DeGiro, one row per product."""
    if degiro_conn is None:
        degiro_conn = get_degiro_connection()
    if isinstance(product_ids, int):
        product_ids = [product_ids]
    # FETCH PRODUCT INFO
    product_info = degiro_conn.get_products_info(
        product_list=product_ids,
        raw=False,
    )
    # return as dataframe
    product_info_df = pd.DataFrame.from_dict({k: v.__dict__ for k, v in product_info.data.items()}, orient='index')
    return product_info_df


def save_product_info(account: Accounts, product_info_df: pd.DataFrame):
    """Save the product information of an account."""
    save_df_to_excel(df=product_info_df, file_name=f'{account.name}_products_info', folder_name=DATA_DIR)


def fetch_portfolio_products_info(account: Accounts, degiro_conn: API | None = None):
    """Download and save the information of the products traded in an account."""
    tx_history_df = load_tx_history(account=account)
    product_ids = list(set(tx_history_df['product_id'].astype(int).to_list()))
    product_df = fetch_product_info(degiro_conn=degiro_conn, product_ids=product_ids)
    save_product_info(account=account, product_info_df=product_df)


def load_portfolio_products(account: Accounts) -> pd.DataFrame:
    """Saved information of the products traded in an account."""
    product_df = load_df_from_excel(file_name=f'{account.name}_products_info', folder_name=DATA_DIR)
    return product_df


def adjust_prod_column_labels(prod_df: pd.DataFrame) -> pd.DataFrame:
    """Fill missing symbols and names with the ISIN; symbols shared by several products get their currency appended."""
    prod_df[Product.symbol.name] = prod_df[Product.symbol.name].fillna(prod_df[Product.isin.name])
    prod_df[Product.name.name] = prod_df[Product.name.name].fillna(prod_df[Product.isin.name])
    prod_duplicate = prod_df.duplicated(Product.symbol.name, keep=False)
    prod_df.loc[prod_duplicate, Product.symbol.name] = prod_df.loc[
        prod_duplicate, [Product.symbol.name, Product.currency.name]
    ].agg('_'.join, axis=1)
    return prod_df
