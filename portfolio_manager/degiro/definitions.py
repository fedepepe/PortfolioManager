"""Degiro product types and exchange identifiers."""

from enum import Enum


class ProductTypes:
    """Product types of the Degiro catalog."""

    STOCK = 'STOCK'
    ETF = 'ETF'
    BOND = 'BOND'
    WARRANT = 'WARRANT'
    INDEX = 'INDEX'
    CURRENCY = 'CURRENCY'
    FUTURE = 'FUTURE'
    OPTION = 'OPTION'
    FUND = 'FUND'
    LEVERAGED = 'LEVERAGED'
    CASH = 'CASH'


class Exchanges(Enum):
    """Degiro exchange ids."""

    XET = 194
    TDG = 196
    EAM = 200
    LSE = 570
    MIL = 608
    SWX = 947
