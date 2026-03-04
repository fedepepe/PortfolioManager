from typing import Callable, Optional, List, Tuple, Dict

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from definitions import RISK_FREE_RATE, PortfolioAllocationStrats
from utils.date_utils import ANN_FACTOR_DICT


def compute_pf_ret(w, mu):
	return w.dot(mu.T)


def compute_neg_pf_ret(w, args):
	return - compute_pf_ret(w, args[0])


def compute_pf_var(w, cov_mat: np.ndarray):
	return w.dot(cov_mat).dot(w.T)


def compute_pf_vol(w, cov_mat: np.ndarray):
	return np.sqrt(compute_pf_var(w, cov_mat))


def pf_volatility(w, args):
	return compute_pf_vol(w, args[1])


def neg_pf_sharpe(w, args):
	mu = args[0]
	cov_mat = args[1]
	risk_free_rate = args[3]
	pf_ret = compute_pf_ret(w, mu)
	pf_vol = compute_pf_vol(w, cov_mat)
	sr = (pf_ret - risk_free_rate) / pf_vol
	return -sr


def pf_var_contr(w, cov_mat: np.ndarray):
	return np.multiply(w.T, cov_mat.dot(w.T))


def vol_risk_parity_obj_fun(w, args):
	cov_mat = args[1]
	risk_budget = args[2]
	risk_budget = risk_budget / sum(risk_budget)
	risk_contrib = pf_var_contr(w, cov_mat) / compute_pf_var(w, cov_mat)
	return sum(np.square(risk_contrib - risk_budget))


class PortfolioOptimizer:
	def __init__(self,
	             optimization_type: PortfolioAllocationStrats,
	             returns: Optional[pd.DataFrame] = pd.DataFrame(),
	             min_pf_exposure: float = 0.0,
	             max_pf_exposure: float = 1.0,
	             bounds_weights: Optional[Tuple[float, float]] = None,
	             weights_init: Optional[np.ndarray] = None,
	             bounds_asset_class: Optional[List[float | Tuple[float, float]]] = None,
	             asset_class_mat: Optional[np.ndarray] = None,
	             target_vol: Optional[float] = None,
	             risk_budget: Optional[Dict[str, float]] = None,
	             risk_free_rate: Optional[float] = 0):
		self.reset(optimization_type=optimization_type,
		           returns=returns,
		           min_pf_exposure=min_pf_exposure,
		           max_pf_exposure=max_pf_exposure,
		           bounds_weights=bounds_weights,
		           weights_init=weights_init,
		           bounds_asset_class=bounds_asset_class,
		           asset_class_mat=asset_class_mat,
		           target_vol=target_vol,
		           risk_budget=risk_budget,
		           risk_free_rate=risk_free_rate)

	def reset(self,
	          optimization_type: PortfolioAllocationStrats,
	          returns: Optional[pd.DataFrame] = pd.DataFrame(),
	          min_pf_exposure: float = 0.0,
	          max_pf_exposure: float = 1.0,
	          bounds_weights: Optional[Tuple[float, float]] = None,
	          weights_init: Optional[np.ndarray] = None,
	          bounds_asset_class: Optional[List[float | Tuple[float, float]]] = None,
	          asset_class_mat: Optional[np.ndarray] = None,
	          target_vol: Optional[float] = None,
	          risk_budget: Optional[Dict[str, float]] = None,
	          risk_free_rate: Optional[float] = 0):
		self.optimization_type = optimization_type
		self.returns = returns
		self.min_pf_exposure = min_pf_exposure
		self.max_pf_exposure = max_pf_exposure
		self.returns_clean = self.clean_returns_df(severity='low')
		self.mu = self.returns_clean.mean().values
		self.cov_mat = self.returns_clean.cov().values
		self.bounds_weights = bounds_weights
		self.weights_init = weights_init
		self.bounds_asset_class = bounds_asset_class
		self.asset_class_mat = asset_class_mat
		self.target_vol = target_vol
		if risk_budget is not None:
			self.risk_budget = np.array([risk_budget[asset] for asset in self.returns_clean.columns])
		else:
			self.risk_budget = None
		self.optim_fun_args = [self.mu, self.cov_mat, self.risk_budget, risk_free_rate]
		self.weights_curr = None

	def get_constraints(self):
		constr_exposure = ({'type': 'ineq', 'fun': lambda x: self.max_pf_exposure - np.sum(x)},
		                   {'type': 'ineq', 'fun': lambda x: np.sum(x) - self.min_pf_exposure})
		if self.bounds_weights is not None:
			constr_bounds_weights = ({'type': 'ineq', 'fun': lambda x: x - self.bounds_weights[0]},
			                         {'type': 'ineq', 'fun': lambda x: self.bounds_weights[1] - x})
		else:
			constr_bounds_weights = ()
		if self.target_vol is not None:
			constr_target_vol = (
			{'type': 'ineq', 'fun': lambda x: 1.05 * self.target_vol - compute_pf_vol(x, self.cov_mat)},
			{'type': 'ineq', 'fun': lambda x: - 0.95 * self.target_vol + compute_pf_vol(x, self.cov_mat)})
		else:
			constr_target_vol = ()
		if self.bounds_asset_class is not None:
			def get_asset_class_constr(x: np.ndarray, bound_asset_class: float, asset_class_vec: np.ndarray):
				return bound_asset_class - asset_class_vec.dot(x)

			if all(isinstance(b, float) for b in self.bounds_asset_class):
				constr_asset_class = tuple([{'type': 'ineq',
				                             'fun': get_asset_class_constr,
				                             'args': (self.bounds_asset_class[i], self.asset_class_mat[i, :])} for i in
				                            range(len(self.bounds_asset_class))])
			elif all(isinstance(b, tuple) for b in self.bounds_asset_class):
				constr_asset_class = []
				for i in range(len(self.bounds_asset_class)):
					if self.bounds_asset_class[i][0] is not None:  # lower bounds
						constr_asset_class.append({'type': 'ineq', 'fun': get_asset_class_constr, 'args': (
						- self.bounds_asset_class[i][0], - self.asset_class_mat[i, :])})
					if self.bounds_asset_class[i][1] is not None:  # upper bounds
						constr_asset_class.append({'type': 'ineq', 'fun': get_asset_class_constr,
						                           'args': (self.bounds_asset_class[i][1], self.asset_class_mat[i, :])})
				constr_asset_class = tuple(constr_asset_class)
			else:
				raise NotImplementedError
		else:
			constr_asset_class = ()
		return constr_exposure + constr_bounds_weights + constr_target_vol + constr_asset_class

	def _compute_optim_portfolio(self, fun: Callable):
		return minimize(fun=fun,
		                x0=self.weights_init,
		                args=self.optim_fun_args,
		                method='trust-constr',
		                constraints=self.get_constraints(),
		                tol=1e-7,
		                options={'disp': False, 'maxiter': 5000, 'verbose': 1})

	def clean_returns_df(self, severity: str = 'high') -> pd.DataFrame:
		if severity == 'high':
			returns_clean = self.returns.dropna(axis=1)
		elif severity == 'low':
			returns_clean = self.returns.dropna(axis=0)
			returns_clean = returns_clean.dropna(axis=1, how='all')
		else:
			raise NotImplementedError
		return returns_clean

	def compute_optimized_portfolio(self):
		try:
			if self.weights_init is None:
				if self.weights_curr is not None:
					self.weights_init = self.weights_curr.copy()
				else:
					self.weights_init = np.array([1 / self.returns_clean.shape[1]] * self.returns_clean.shape[1])
			if self.optimization_type == PortfolioAllocationStrats.MAX_RET:
				optim = self._compute_optim_portfolio(fun=compute_neg_pf_ret)
			elif self.optimization_type == PortfolioAllocationStrats.MIN_VAR:
				optim = self._compute_optim_portfolio(fun=pf_volatility)
			elif self.optimization_type == PortfolioAllocationStrats.MAX_SHARPE:
				optim = self._compute_optim_portfolio(fun=neg_pf_sharpe)
			elif self.optimization_type == PortfolioAllocationStrats.RISK_PARITY:
				optim = self._compute_optim_portfolio(fun=vol_risk_parity_obj_fun)
			else:
				raise TypeError
			if not optim.success:
				print('Warning! Optimization was not successful.')
				print(f'Status {optim.status}. Message: {optim.message}')
				print(f'Returns: {self.returns_clean}')
				print(f'Covariance matrix: {self.cov_mat}')
				print(f'Mean return vector: {self.mu}')
			print('---------------------------------------')
			print(f'Optimized portfolio weights: {optim.x}')
			if self.optimization_type == PortfolioAllocationStrats.RISK_PARITY:
				risk_contrib = pf_var_contr(optim.x, self.cov_mat) / compute_pf_vol(optim.x, self.cov_mat)
				print(f'Optimized risk contributions: {risk_contrib}')
			print('---------------------------------------')
			self.weights_curr = optim.x
			return pd.Series(optim.x, index=self.returns_clean.columns, name='weights')
		except (ValueError, ZeroDivisionError) as e:
			if not self.returns_clean.empty:
				print(f'Warning! Found an issue with running the optimization ({e}).')
			else:
				print('Warning! Optimization cannot be performed. Return vector is empty.')
			return pd.Series(np.nan, index=self.returns.columns, name='weights')


def compute_weights_optim_portfolio(allocation_method: PortfolioAllocationStrats,
                                    prices: pd.DataFrame,
                                    sampling_freq: str,
                                    optimization_freq: str,
                                    extra_args: Optional[Dict] = None
                                    ) -> pd.DataFrame:
	extra_args = extra_args or {}
	weights_df = pd.DataFrame().reindex_like(prices.resample(optimization_freq).last())
	args = dict(optimization_type=allocation_method,
	            min_pf_exposure=extra_args.get('min_pf_exposure', 1.0),
	            max_pf_exposure=extra_args.get('max_pf_exposure', 0.0),
	            bounds_weights=(extra_args.get('min_pf_exposure', 1.0), extra_args.get('max_asset_exposure', 0.0)))
	if extra_args.get('bounds_asset_class', None) is not None:
		asset_class_mat = np.zeros(shape=(len(extra_args.get('bounds_asset_class', None)), len(prices.columns)))
		for n in range(len(extra_args.get('bounds_asset_class', None))):
			asset_class_mat[n, :] = [1 if c == list(extra_args.get('bounds_asset_class', None).keys())[n] else 0 for c in
			                         extra_args.get('asset_classes', None)]
		args['bounds_asset_class'] = list(extra_args.get('bounds_asset_class', None).values())
		args['asset_class_mat'] = asset_class_mat
	if allocation_method == PortfolioAllocationStrats.MAX_RET:
		args['target_vol'] = extra_args.get('target_vol', None) / np.sqrt(ANN_FACTOR_DICT[sampling_freq])
	elif allocation_method == PortfolioAllocationStrats.MIN_VAR:
		pass
	elif allocation_method == PortfolioAllocationStrats.MAX_SHARPE:
		args['risk_free_rate'] = np.power(1. + RISK_FREE_RATE, 1. / ANN_FACTOR_DICT[sampling_freq]) - 1
	elif allocation_method == PortfolioAllocationStrats.RISK_PARITY:
		if extra_args.get('risk_budget', None) is None:
			args['risk_budget'] = {asset: 1 for asset in prices.columns}
		else:
			args['risk_budget'] = extra_args.get('risk_budget', None)
	print(f'Optimization frequency: {optimization_freq}')
	pf_optimizer = PortfolioOptimizer(**args)
	print(args)
	for idx in prices.resample(optimization_freq).last().index:
		print(f'Running optimization as of {idx}...')
		returns_df = prices[prices.index <= idx].pct_change()
		args['returns'] = returns_df
		pf_optimizer.reset(**args)
		optim_weights = pf_optimizer.compute_optimized_portfolio()
		if extra_args.get('max_asset_num', None) is not None:
			optim_weights = optim_weights.mask(
				optim_weights.rank(method='min', ascending=False) > extra_args.get('max_asset_num', None), 0)
			print('-----------------------------')
			print(f'Optimized weights: {optim_weights.values}')
			print('-----------------------------')
		weights_df.loc[idx, :] = optim_weights
	return weights_df
