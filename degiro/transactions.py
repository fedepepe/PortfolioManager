import logging
from datetime import date
from enum import Enum
from typing import Any, Optional

import pandas as pd
from degiro_connector.trading.api import API
from degiro_connector.trading.models.account import OverviewRequest
from degiro_connector.trading.models.transaction import HistoryRequest

from utils import file_utils as fu
from config.definitions import DATA_DIR
from config.accounts import Accounts
from degiro.degiro_connection import get_degiro_connection

logging.basicConfig(level=logging.DEBUG)


class TxHistFields:
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
        df[col] = pd.DatetimeIndex(pd.to_datetime(df[col], utc=True)).tz_convert(None)
    df = df.set_index('date')
    df = df.sort_index()
    return df


def fetch_tx_history(account: Accounts,
                     degiro_conn: Optional[API] = None) -> pd.DataFrame:
    if degiro_conn is None:
        degiro_conn = get_degiro_connection()
    # FETCH ACCOUNT OVERVIEW
    transactions_history = degiro_conn.get_transactions_history(
        transaction_request=HistoryRequest(
            from_date=date(year=date.today().year - 10, month=1, day=1),
            to_date=date.today(),
        ),
        raw=False,
    )
    tx_history_df = field_list_to_df(data=transactions_history.data)
    fu.save_df_to_excel(df=tx_history_df, file_name=f'{account.name}_tx_hist', folder_name=DATA_DIR)
    return tx_history_df


def load_tx_history(account: Accounts) -> pd.DataFrame:
    tx_history_df = fu.load_df_from_excel(file_name=f'{account.name}_tx_hist', folder_name=DATA_DIR)
    tx_history_df = tx_history_df.astype({"product_id": int})
    return tx_history_df


def fetch_account_movements(account: Accounts,
                            degiro_conn: Optional[API] = None):
    if degiro_conn is None:
        degiro_conn = get_degiro_connection()
    # FETCH ACCOUNT OVERVIEW
    overview_request = OverviewRequest(
        from_date=date(year=date.today().year - 10, month=1, day=1),
        to_date=date.today(),
    )

    account_overview = degiro_conn.get_account_overview(
        overview_request=overview_request,
        raw=False,
    )
    account_movements_df = field_list_to_df(data=account_overview.cash_movements)
    fu.save_df_to_excel(df=account_movements_df, file_name=f'{account.name}_movements', folder_name=DATA_DIR)
    return account_movements_df


def load_account_movements(account: Accounts) -> pd.DataFrame:
    account_movements_df = fu.load_df_from_excel(file_name=f'{account.name}_movements', folder_name=DATA_DIR)
    return account_movements_df
