import numpy as np
import pandas as pd
import pytest

from portfolio.portfolio_definitions import Portfolio, PortfolioRebalanceType

TICKERS = ['A', 'B', 'C']


def make_portfolio(**kwargs) -> Portfolio:
    params = dict(tickers=TICKERS, initial_cash_balance=1e6, txn_costs_prop_bp=10)
    params.update(kwargs)
    return Portfolio(**params)


def test_txn_costs_proportional_and_fixed_only_on_traded_assets():
    pf = make_portfolio(txn_costs_fixed=5.)
    costs = pf.compute_txn_costs(np.array([-10_000., 0., 2_000.]))
    np.testing.assert_allclose(costs, [10. + 5., 0., 2. + 5.])


def test_txn_costs_capped_per_asset():
    pf = make_portfolio(txn_costs_fixed=5., txn_costs_max=12.)
    costs = pf.compute_txn_costs(np.array([-10_000., 0., 2_000.]))
    np.testing.assert_allclose(costs, [12., 0., 7.])


def test_no_cap_by_default():
    assert make_portfolio().txn_costs_max is None


def test_rebalance_keeps_min_cash_ratio_and_pays_costs():
    pf = make_portfolio(min_cash_ratio=0.001)
    prices = pd.Series([10., 20., 50.], index=TICKERS)
    pf.rebalance(current_prices=prices, target_exp=pd.Series([0.5, 0.3, 0.2], index=TICKERS))
    nav = pf.get_nav(prices)
    cash = pf.get_current_cash_balance()
    assert cash >= 0.
    # the targets apply to the NAV less the cash buffer and the costs
    np.testing.assert_allclose(pf.get_effective_weights(prices), [0.5, 0.3, 0.2], atol=2e-3)
    np.testing.assert_allclose(pf.txn_costs.sum(), 1e-3 * np.abs(pf.txn_values).sum())
    assert nav == pytest.approx(1e6 - pf.txn_costs.sum())


def test_minimal_rebalance_trades_only_assets_beyond_tolerance():
    pf = make_portfolio(max_target_dev=0.01)
    prices = pd.Series([10., 20., 50.], index=TICKERS)
    target = pd.Series([0.4, 0.3, 0.25], index=TICKERS)  # 5% cash, so a minimal rebalance can buy
    pf.rebalance(current_prices=prices, target_exp=target)
    units_before = pf.current_units.copy()
    # C falls 8%: its weight drifts 1.5 points from target (beyond), A and B less than 1 point (within)
    moved = pd.Series([10.02, 20., 46.], index=TICKERS)
    pf.rebalance(current_prices=moved, target_exp=target)
    assert pf.current_units[0] == units_before[0]
    assert pf.current_units[1] == units_before[1]
    assert pf.current_units[2] > units_before[2]


def test_rebalance_never_overdraws_cash():
    # fully invested with no buffer: a minimal rebalance lacking cash falls back to a full rebalance
    pf = make_portfolio(max_target_dev=0.01, min_cash_ratio=0.001, txn_costs_fixed=20.)
    target = pd.Series([0.6, 0.2, 0.2], index=TICKERS)
    rng = np.random.default_rng(1)
    prices = pd.Series([10., 20., 50.], index=TICKERS)
    for _ in range(100):
        prices = prices * np.exp(rng.normal(0., 0.02, size=3))
        pf.rebalance(current_prices=prices, target_exp=target)
        assert pf.get_current_cash_balance() >= 0.


def test_full_rebalance_type_trades_everything():
    pf = make_portfolio()
    prices = pd.Series([10., 20., 50.], index=TICKERS)
    target = pd.Series([0.4, 0.3, 0.3], index=TICKERS)
    pf.rebalance(current_prices=prices, target_exp=target)
    units = pf.compute_current_units(prices * 1.001, target_exp=target, rebalance_type=PortfolioRebalanceType.FULL)
    assert (units != pf.current_units).all()
