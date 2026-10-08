"""Objective functions of the optimizer; args = [expected returns, covariance matrix, risk budget, risk-free rate]."""

import numpy as np


def vol_risk_contr(w, cov_mat):
    """Share of the portfolio variance contributed by each asset."""
    s = compute_pf_vol(w, cov_mat)
    vol_risk_contr_i = np.multiply(w.T, cov_mat.dot(w.T)) / s**2
    return vol_risk_contr_i


def compute_pf_ret(w, mu):
    """Expected return of the portfolio."""
    return w.dot(mu.T)


def compute_neg_pf_ret(w, args):
    """Negative expected return (to maximize the return)."""
    return -compute_pf_ret(w, args[0])


def compute_pf_var(w, cov_mat: np.ndarray):
    """Variance of the portfolio."""
    return w.dot(cov_mat).dot(w.T)


def compute_pf_vol(w, cov_mat: np.ndarray):
    """Volatility of the portfolio."""
    return np.sqrt(compute_pf_var(w, cov_mat))


def pf_volatility_obj_fun(w, args):
    """Volatility of the portfolio (to minimize the variance)."""
    return compute_pf_vol(w, args[1])


def neg_pf_sharpe_obj_fun(w, args):
    """Negative Sharpe ratio (to maximize the Sharpe ratio)."""
    mu = args[0]
    cov_mat = args[1]
    risk_free_rate = args[3]
    pf_ret = compute_pf_ret(w, mu)
    pf_vol = compute_pf_vol(w, cov_mat)
    sr = (pf_ret - risk_free_rate) / pf_vol
    return -sr


def pf_var_contr(w, cov_mat: np.ndarray):
    """Contribution of each asset to the portfolio variance."""
    return np.multiply(w.T, cov_mat.dot(w.T))


def vol_risk_parity_obj_fun(w, args):
    """Squared distance of the variance contributions from the risk budget."""
    cov_mat = args[1]
    risk_budget = args[2]
    risk_budget = risk_budget / sum(risk_budget)
    risk_contrib = pf_var_contr(w, cov_mat) / compute_pf_var(w, cov_mat)
    return sum(np.square(risk_contrib - risk_budget))
