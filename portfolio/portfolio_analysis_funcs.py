import numpy as np


def pf_vol(w, cov_mat):
	s = np.sqrt(w.dot(cov_mat).dot(w.T))
	return s


def neg_pf_sharpe(w, args):
	mu = args[0]
	covar = args[1]
	s = np.sqrt(w.dot(covar).dot(w.T))
	pf_ret = w.dot(mu.T)
	sr = pf_ret / s
	return -sr


def vol_risk_contr(w, cov_mat):
	s = pf_vol(w, cov_mat)
	vol_risk_contr_i = np.multiply(w.T, cov_mat.dot(w.T)) / s ** 2
	return vol_risk_contr_i


def vol_risk_parity_obj_fun(w, args):
	cov_mat = args[0]
	risk_budget = args[1]
	s = pf_vol(w, cov_mat)
	risk_contrib = vol_risk_contr(w, cov_mat)
	risk_target = np.multiply(s, risk_budget)
	return sum(np.square(risk_contrib - risk_target))
