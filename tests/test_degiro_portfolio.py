import pandas as pd
import pytest

from degiro.portfolio_backtest import PortfolioDegiro
from degiro.transactions import TxHistFields as F


def trade(product_id, quantity, price, total):
    return {
        F.product_id: product_id,
        F.quantity: quantity,
        F.price: price,
        F.total: total,
        F.total_in_base_currency: total,
        F.total_fees_in_base_currency: -1.0,
    }


def test_units_from_trades():
    pf = PortfolioDegiro(tickers=[1, 2, 3], initial_cash_balance=10_000.0)
    pf.rebalance(
        tx_hist_df=pd.DataFrame(
            [
                trade(1, 10, 25.0, -250.0),  # stock: quantity x price = total
                trade(2, 3, 33.333, -100.0),  # rounded total: units still equal the quantity
                trade(3, 2000, 98.5, -1970.0),  # bond quoted in % of the nominal: units from the total
            ]
        )
    )
    assert pf.current_units[0] == 10
    assert pf.current_units[1] == 3
    assert pf.current_units[2] == pytest.approx(1970.0 / 98.5)
    # cash: trade totals and fees
    assert pf.get_current_cash_balance() == pytest.approx(10_000.0 - 250.0 - 100.0 - 1970.0 - 3.0)
