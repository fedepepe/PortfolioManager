"""Models of the Degiro responses that differ from those of degiro-connector.

degiro-connector (up to 3.0.36 at least) declares the contract size of a product as an integer, but bonds have
fractional contract sizes (e.g. 0.01: a fraction of the nominal per unit), so its validation of the products
information fails for them and the whole request returns nothing. The products information is therefore requested raw
and validated with the models below: the same as degiro-connector's, with the contract size as a number.
"""

from degiro_connector.trading.api import API
from degiro_connector.trading.models.product import ProductInfo, ProductItem
from pydantic import Field


class DegiroProductItem(ProductItem):
    """The information of a Degiro product (degiro-connector's ProductItem), with a fractional contract size."""

    contract_size: float | None = Field(default=None)


class DegiroProductInfo(ProductInfo):
    """The information of Degiro products by product id (degiro-connector's ProductInfo), see DegiroProductItem."""

    data: dict[int, DegiroProductItem] = Field(default_factory=dict)


def get_products_info(degiro_conn: API, product_ids: list[int]) -> DegiroProductInfo | None:
    """Information of Degiro products (None if the request fails: degiro-connector logs the error)."""
    raw = degiro_conn.get_products_info(product_list=product_ids, raw=True)
    return DegiroProductInfo.model_validate(raw) if raw is not None else None
