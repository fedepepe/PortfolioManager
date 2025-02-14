import logging
from enum import Enum
from typing import List, Optional

import pandas as pd
from degiro_connector.trading.api import API

import file_utils as fu
from definitions import DATA_DIR, DEFAULT_PORTFOLIO_NAME
from degiro_connection import get_degiro_connection
from product_definitions import ProductTypes
from sql import insert_product, query_products
from transactions import load_tx_history

logging.basicConfig(level=logging.DEBUG)


def fetch_full_product_catalog(degiro_conn: Optional[API] = None):
    if degiro_conn is None:
        degiro_conn = get_degiro_connection()
    # FETCH PRODUCT INFO
    n_start = 0  # get_max_product_id()
    if n_start is None:
        n_start = 0
    n = n_start
    while True:
        product_info = (degiro_conn
                        .get_products_info(product_list=[i for i in range(n, n + 1000)], raw=False, ))
        if hasattr(product_info, 'data'):
            for prod_id in product_info.data:
                if (product_info.data[prod_id].product_type in [ProductTypes.CURRENCY,
                                                                ProductTypes.STOCK,
                                                                ProductTypes.ETF,
                                                                ProductTypes.BOND] and
                        product_info.data[prod_id].active is True):
                    insert_product(product_info.data[prod_id])
        if n > n_start + 40e6:
            break
        n += 1000
    return product_info


def fetch_single_product(degiro_conn: API, product_id: int):
    product_info = degiro_conn.get_products_info(product_list=[product_id], raw=False)
    if hasattr(product_info, 'data'):
        insert_product(product_info.data[product_id])


def fetch_product_info(degiro_conn: Optional[API] = None,
                       product_ids: int | List[int] = 11853206
                       ) -> pd.DataFrame:
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


def save_product_info(product_info_df: pd.DataFrame,
                      portfolio_name: str = DEFAULT_PORTFOLIO_NAME):
    fu.save_df_to_excel(df=product_info_df, file_name=f'{portfolio_name}_products_info', folder=DATA_DIR)


def fetch_portfolio_products(degiro_conn: Optional[API] = None,
                             portfolio_name: str = DEFAULT_PORTFOLIO_NAME):
    tx_history_df = load_tx_history(portfolio_name=portfolio_name)
    product_ids = list(set(tx_history_df['product_id'].astype(int).to_list()))
    product_df = fetch_product_info(degiro_conn=degiro_conn, product_ids=product_ids)
    save_product_info(product_info_df=product_df, portfolio_name=portfolio_name)


def load_portfolio_products(portfolio_name: str = DEFAULT_PORTFOLIO_NAME) -> pd.DataFrame:
    product_df = fu.load_df_from_excel(file_name=f'{portfolio_name}_products_info', folder=DATA_DIR)
    return product_df


class UnitTests(Enum):
    FETCH_FULL_PRODUCT_CATALOG = 1
    LOAD_ETF_CATALOG = 2


def run_unit_test(unit_test: UnitTests):
    if unit_test == UnitTests.FETCH_FULL_PRODUCT_CATALOG:
        fetch_full_product_catalog()
    elif unit_test == UnitTests.LOAD_ETF_CATALOG:
        results_df = query_products(product_type=ProductTypes.ETF, tradable=True)
        print(results_df)
    else:
        raise NotImplementedError


if __name__ == '__main__':
    unit_test = UnitTests.FETCH_FULL_PRODUCT_CATALOG
    run_unit_test(unit_test=unit_test)
