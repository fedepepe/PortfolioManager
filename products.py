import logging
from typing import List, Optional

import pandas as pd

import file_utils as fu
from degiro_connection import get_connection
from sql import insert_product, get_product

logging.basicConfig(level=logging.DEBUG)


def fetch_full_product_catalog():
    # FETCH PRODUCT INFO
    n = 0
    session_degiro = get_connection()
    while True:
        product_info = (session_degiro
                        .get_products_info(product_list=[i for i in range(n, n + 1000)], raw=False, ))
        if hasattr(product_info, 'data'):
            for id in product_info.data:
                insert_product(product_info.data[id])
        if n > 2000000:
            break
        n += 1000
    return product_info


def read_product_catalog(product_name: Optional[str] = None,
                         product_type: Optional[str] = None
                         ) -> pd.DataFrame:
    return get_product(product_name=product_name, product_type=product_type)


def get_product_info(product_ids: int | List[int] = 11853206):
    if isinstance(product_ids, int):
        product_ids = [product_ids]

    # FETCH PRODUCT INFO
    product_info = get_connection().get_products_info(
        product_list=product_ids,
        raw=False,
    )
    # SAVE PRODUCT INFO
    product_info_df = pd.DataFrame.from_dict({k: v.__dict__ for k, v in product_info.data.items()}, orient='index')
    save_product_info(product_info_df)
    return product_info


def save_product_info(product_info_df: pd.DataFrame):
    try:
        df_old = fu.load_df_from_excel(file_name='products_info', folder='data')
    except FileNotFoundError:
        df_old = pd.DataFrame()
    df = pd.concat([df_old, product_info_df], axis=1)
    df = df.loc[:, ~df.columns[::-1].duplicated()[::-1]]
    fu.save_df_to_excel(df=df, file_name='products_info', folder='data')


if __name__ == '__main__':
    read_product_catalog(product_type='CURRENCY')
    # fetch_full_product_catalog()
