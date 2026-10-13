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


# Yahoo Finance suffix of the Degiro exchanges that Yahoo lists (e.g. EUNL on Xetra is EUNL.DE)
YAHOO_SUFFIXES = {
    Exchanges.XET: 'DE',
    Exchanges.EAM: 'AS',
    Exchanges.LSE: 'L',
    Exchanges.MIL: 'MI',
    Exchanges.SWX: 'SW',
}


def exchange_name(exchange_id) -> str:
    """Degiro abbreviation of an exchange id (e.g. XET for Xetra), the id itself if unknown."""
    return next((exchange.name for exchange in Exchanges if exchange.value == exchange_id), str(exchange_id))


def yahoo_suffix(exchange_id) -> str | None:
    """Yahoo Finance suffix of a Degiro exchange id, None if Yahoo does not list it."""
    return next((suffix for exchange, suffix in YAHOO_SUFFIXES.items() if exchange.value == exchange_id), None)
