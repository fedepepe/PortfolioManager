import logging
from typing import Any
from datetime import date
from enum import Enum

import pandas as pd
from degiro_connector.trading.models.transaction import HistoryRequest
from degiro_connector.trading.models.account import OverviewRequest

import file_utils as fu
from degiro_connection import TRADING_API
from definitions import DATA_DIR

logging.basicConfig(level=logging.DEBUG)


class TxHistDataFields(str, Enum):
    date = 'date'
    auto_fx_fee_in_base_currency = 'auto_fx_fee_in_base_currency'
    buysell = 'buysell'
    counter_party = 'counter_party'
    executing_entity_id = 'executing_entity_id'
    fee_in_base_currency = 'fee_in_base_currency'
    fx_rate = 'fx_rate'
    gross_fx_rate = 'gross_fx_rate'
    id = 'id'
    nett_fx_rate = 'nett_fx_rate'
    order_type_id = 'order_type_id'
    price = 'price'
    product_id = 'product_id'
    quantity = 'quantity'
    total = 'total'
    total_fees_in_base_currency = 'total_fees_in_base_currency'
    total_in_base_currency = 'total_in_base_currency'
    total_plus_all_fees_in_base_currency = 'total_plus_all_fees_in_base_currency'
    total_plus_fee_in_base_currency = 'total_plus_fee_in_base_currency'
    transfered = 'transfered'
    trading_venue = 'trading_venue'
    transaction_type_id = 'transaction_type_id'
    symbol = 'symbol'


class CashMovements(str, Enum):
    date = 'date'
    balance = 'balance'
    change = 'change'
    currency = 'currency'
    description = 'description'
    id = 'id'
    product_id = 'product_id'
    type = 'type'
    value_date = 'value_date'


def field_list_to_df(data: Any) -> pd.DataFrame:
    df = pd.DataFrame()
    for field in data:
        columns, values = zip(*field)
        ser = pd.Series(values, columns)
        df = pd.concat([df, ser.to_frame().T])
    for col in [c for c in df.columns if 'date' in c.lower()]:
        df[col] = pd.DatetimeIndex(df[col]).tz_convert(None)
    df = df.set_index('date')
    df = df.sort_index()
    return df


def fetch_tx_history() -> pd.DataFrame:
    # FETCH ACCOUNT OVERVIEW
    transactions_history = TRADING_API.get_transactions_history(
        transaction_request=HistoryRequest(
            from_date=date(year=date.today().year - 10, month=1, day=1),
            to_date=date.today(),
        ),
        raw=False,
    )

    tx_history_df = field_list_to_df(data=transactions_history.data)
    fu.save_df_to_excel(df=tx_history_df, file_name='tx_history', folder=DATA_DIR)
    return tx_history_df


def load_tx_history() -> pd.DataFrame:
    tx_history_df = fu.load_df_from_excel(file_name='tx_history', folder=DATA_DIR)
    return tx_history_df


def fetch_account_movements():
    # FETCH ACCOUNT OVERVIEW
    overview_request = OverviewRequest(
        from_date=date(year=date.today().year - 10, month=1, day=1),
        to_date=date.today(),
    )

    account_overview = TRADING_API.get_account_overview(
        overview_request=overview_request,
        raw=False,
    )
    account_movements_df = field_list_to_df(data=account_overview.cash_movements)
    fu.save_df_to_excel(df=account_movements_df, file_name='account_movements', folder=DATA_DIR)
    return account_movements_df


def load_account_movements() -> pd.DataFrame:
    account_movements_df = fu.load_df_from_excel(file_name='account_movements', folder=DATA_DIR)
    return account_movements_df


if __name__ == '__main__':
    load_account_movements()
