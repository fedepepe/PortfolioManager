import pydantic
import pytest
from degiro_connector.trading.models.product import ProductInfo

import portfolio_manager.storage.queries as sql
from portfolio_manager.degiro.models import DegiroProductInfo
from portfolio_manager.degiro.products import fetch_product_info

# products information as Degiro returns it: a bond whose contract size is a fraction of its nominal, and an ETF
RAW_PRODUCTS = {
    'data': {
        '1001': {
            'id': '1001',
            'name': 'BTP 2030',
            'isin': 'IT0005413171',
            'productType': 'BOND',
            'contractSize': 0.01,
            'currency': 'EUR',
            'active': True,
            'vwdId': '360114899',
        },
        '1002': {
            'id': '1002',
            'name': 'iShares Core MSCI World',
            'isin': 'IE00B4L5Y983',
            'productType': 'ETF',
            'contractSize': 1.0,
            'currency': 'EUR',
            'active': True,
        },
    }
}


class FakeConnection:
    # answers the products information request like degiro-connector's API, raw or validated by its model
    def __init__(self):
        self.raw_requests = []

    def get_products_info(self, product_list, raw=False):
        self.raw_requests.append(raw)
        return RAW_PRODUCTS if raw else ProductInfo.model_validate(RAW_PRODUCTS)


def test_degiro_connector_still_rejects_fractional_contract_sizes():
    # when this fails, degiro-connector accepts them: DegiroProductInfo (degiro.models) can be removed
    with pytest.raises(pydantic.ValidationError, match='contract_size|contractSize'):
        ProductInfo.model_validate(RAW_PRODUCTS)


def test_fractional_contract_size_of_a_bond_accepted():
    info = DegiroProductInfo.model_validate(RAW_PRODUCTS)
    bond = info.data[1001]
    assert bond.contract_size == 0.01 and bond.isin == 'IT0005413171' and bond.product_type == 'BOND'
    assert info.data[1002].contract_size == 1.0


def test_products_requested_raw_and_stored_with_their_contract_size(temp_db):
    connection = FakeConnection()
    products = fetch_product_info(degiro_conn=connection, product_ids=[1001, 1002])
    assert connection.raw_requests == [True]  # degiro-connector's own validation is not used
    assert products.loc[1001, 'contract_size'] == 0.01
    sql.upsert_products(products)
    stored = sql.query_products(product_id=[1001, 1002])
    assert stored.loc[1001, 'contract_size'] == 0.01  # not cut to an integer
    assert stored.loc[1002, 'contract_size'] == 1.0


def test_failed_request_reported():
    class Offline:
        def get_products_info(self, product_list, raw=False):
            return None  # degiro-connector logs the error and returns nothing

    with pytest.raises(RuntimeError, match='did not return'):
        fetch_product_info(degiro_conn=Offline(), product_ids=[1001])
