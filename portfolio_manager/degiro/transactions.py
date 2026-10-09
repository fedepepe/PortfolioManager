"""Degiro transactions and cash movements of an account, stored in the database."""

import logging
from datetime import date
from enum import Enum
from typing import Any

import pandas as pd
from degiro_connector.trading.api import API
from degiro_connector.trading.models.account import OverviewRequest
from degiro_connector.trading.models.transaction import HistoryRequest

from portfolio_manager.config.accounts import Accounts
from portfolio_manager.config.settings import DATA_DIR
from portfolio_manager.degiro.connection import get_degiro_connection
from portfolio_manager.storage import files as fu
from portfolio_manager.storage.models import DegiroCashMovement, DegiroTransaction
from portfolio_manager.storage.queries import DegiroRecord, query_degiro_records, upsert_degiro_records

logger = logging.getLogger(__name__)


class TxHistFields:
    """Columns of the transaction history."""

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
    """Columns of the cash movements."""

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
    """Frame of Degiro records (lists of field/value pairs); date columns without time zone."""
    df = pd.DataFrame()
    for field in data:
        columns, values = zip(*field, strict=True)
        ser = pd.Series(values, columns)
        df = pd.concat([df, ser.to_frame().T])
    for col in [c for c in df.columns if 'date' in c.lower()]:
        df[col] = pd.DatetimeIndex(pd.to_datetime(df[col], utc=True)).tz_convert(None)
    df = df.set_index('date')
    df = df.sort_index()
    return df


# columns of the saved transactions and cash movements, in the order Degiro returns them (index: date)
TX_HIST_COLUMNS = [v for k, v in vars(TxHistFields).items() if not k.startswith('_') and v not in ('date', 'symbol')]
CASH_MOVEMENTS_COLUMNS = [c.value for c in CashMovements if c != CashMovements.date]


def tx_history_file_name(account: Accounts) -> str:
    """Excel copy of the transactions of an account."""
    return f'{account.name}_tx_hist'


def movements_file_name(account: Accounts) -> str:
    """Excel copy of the cash movements of an account."""
    return f'{account.name}_movements'


def _store_records(model: type[DegiroRecord], account: Accounts, df: pd.DataFrame, from_date: date):
    # stored records are updated and kept when Degiro no longer returns them (older than its ten years)
    stored = query_degiro_records(model, account.name)
    missing = stored.index[~stored['id'].isin(df['id']) & (stored.index >= pd.Timestamp(from_date))]
    if len(missing) > 0:
        logger.warning(
            '%d %s of %s stored but no longer returned by Degiro', len(missing), model.__tablename__, account.name
        )
    upsert_degiro_records(model, account.name, df)


def fetch_tx_history(account: Accounts, degiro_conn: API | None = None) -> pd.DataFrame:
    """Download the transactions of the last ten years into the database, with an Excel copy of all the stored ones."""
    if degiro_conn is None:
        degiro_conn = get_degiro_connection(account=account)
    from_date = date(year=date.today().year - 10, month=1, day=1)
    transactions_history = degiro_conn.get_transactions_history(
        transaction_request=HistoryRequest(from_date=from_date, to_date=date.today()),
        raw=False,
    )
    tx_history_df = field_list_to_df(data=transactions_history.data)
    _store_records(DegiroTransaction, account, tx_history_df, from_date)
    fu.save_df_to_excel(df=load_tx_history(account), file_name=tx_history_file_name(account), folder_name=DATA_DIR)
    return tx_history_df


def load_tx_history(account: Accounts) -> pd.DataFrame:
    """Saved transactions of an account, by date."""
    tx_history_df = query_degiro_records(DegiroTransaction, account.name)[TX_HIST_COLUMNS]
    tx_history_df = tx_history_df.astype({'product_id': int})
    return tx_history_df


def fetch_account_movements(account: Accounts, degiro_conn: API | None = None) -> pd.DataFrame:
    """Download the cash movements of the last ten years into the database, with an Excel copy of all the stored
    ones.
    """
    if degiro_conn is None:
        degiro_conn = get_degiro_connection(account=account)
    from_date = date(year=date.today().year - 10, month=1, day=1)
    account_overview = degiro_conn.get_account_overview(
        overview_request=OverviewRequest(from_date=from_date, to_date=date.today()),
        raw=False,
    )
    account_movements_df = field_list_to_df(data=account_overview.cash_movements)
    _store_records(DegiroCashMovement, account, account_movements_df, from_date)
    fu.save_df_to_excel(
        df=load_account_movements(account), file_name=movements_file_name(account), folder_name=DATA_DIR
    )
    return account_movements_df


def load_account_movements(account: Accounts) -> pd.DataFrame:
    """Saved cash movements of an account, by date."""
    return query_degiro_records(DegiroCashMovement, account.name)[CASH_MOVEMENTS_COLUMNS]
