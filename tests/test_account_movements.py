import pandas as pd
import pytest

from portfolio_manager.backtest.workflows import (
    adjust_tx_for_splits,
    last_complete_date,
    product_changes,
    split_multipliers,
    warn_stale_prices,
)
from portfolio_manager.degiro.transactions import TxHistFields as F


def movements(rows) -> pd.DataFrame:
    # synthetic Degiro cash movements (descriptions in Italian, as in the account)
    df = pd.DataFrame(rows, columns=['date', 'product_id', 'change', 'description'])
    df['date'] = pd.to_datetime(df['date'])
    df['value_date'] = df['date'].dt.normalize()  # Degiro gives both movements of a change the same value date
    return df.set_index('date')


SPLIT = movements(
    [
        (
            '2026-02-23 06:20',
            2,
            9316.8,
            'RETTIFICA PER FRAZIONAMENTO AZIONARIO: 36 Some ETF @ 258,8 EUR (XX0000000002)',
        ),
        (
            '2026-02-23 06:20',
            2,
            -9316.8,
            'RETTIFICA PER FRAZIONAMENTO AZIONARIO: 900 Some ETF @ 10,352 EUR (XX0000000002)',
        ),
    ]
)
CHANGE = movements(
    [
        ('2023-12-06 19:36', 1, 0.0, 'CAMBIO DI PRODOTTO: Vendita 36 Some ETF@171 EUR (XX0000000002)'),
        ('2023-12-06 19:52', 2, 0.0, 'CAMBIO DI PRODOTTO: Acquisto 36 Some ETF@171 EUR (XX0000000002)'),
    ]
)


def trades(product_id, quantities, dates) -> pd.DataFrame:
    return pd.DataFrame(
        {F.product_id: product_id, F.quantity: quantities, F.price: 250.0}, index=pd.to_datetime(dates)
    ).rename_axis('date')


def test_split_multiplier():
    splits = split_multipliers(SPLIT)
    assert splits['product_id'].tolist() == [2]
    assert splits['mult'].iloc[0] == pytest.approx(25.0)  # 36 units became 900


def test_product_changes():
    assert product_changes(CHANGE) == {1: 2}
    assert product_changes(SPLIT) == {}


def test_ordinary_split_is_applied_once():
    tx = trades(2, [36.0, 4.0], ['2025-05-02', '2026-03-02'])  # one trade before the split, one after
    adjust_tx_for_splits(tx, split_multipliers(SPLIT))
    assert tx[F.quantity].tolist() == [900.0, 4.0]
    assert tx[F.price].tolist() == [10.0, 250.0]


def test_split_after_product_change():
    # trades recorded under the old product id are renamed, then converted into post-split units
    tx = trades(1, [36.0], ['2022-01-06'])
    for id_old, id_new in product_changes(CHANGE).items():
        tx[F.product_id] = tx[F.product_id].replace(id_old, id_new)
    adjust_tx_for_splits(tx, split_multipliers(SPLIT))
    assert tx[F.product_id].tolist() == [2]
    assert tx[F.quantity].tolist() == [900.0]


def test_backtest_ends_on_last_day_with_prices_of_held_products():
    days = pd.bdate_range('2026-10-05', periods=4)
    prices = pd.DataFrame({1: [1.0, 1.0, 1.0, 1.0], 2: [2.0, 2.0, 2.0, None], 3: [3.0, None, None, None]}, index=days)
    # product 2 is held, but its price of the last day is missing; product 3 was sold
    tx = trades([1, 2, 3, 3], [10, 5, 4, -4], ['2026-10-05'] * 4)
    assert last_complete_date(prices, tx) == days[2]
    assert last_complete_date(prices, tx[tx[F.product_id] == 3]) == days[3]  # nothing held: all the prices


def test_warning_for_prices_missing_on_the_last_day(caplog):
    days = pd.bdate_range('2026-10-06', periods=4)  # 10-06 .. 10-09
    # A misses 10-08 but has 10-09 already, B has 10-08, C stops on 10-07
    prices = pd.DataFrame(
        {'A': [1.0, 1.0, None, 1.0], 'B': [2.0, 2.0, 2.0, None], 'C': [3.0, 3.0, None, None]}, index=days
    )
    warn_stale_prices(prices, ts_end=days[2], label='Benchmark')
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert 'of A on 2026-10-08 (last before on 2026-10-07)' in messages[0] and 'of C' in messages[1]
