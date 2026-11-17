"""Portfolio optimizer and the optimized weights over time."""

import logging
from collections.abc import Callable
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize

import portfolio_manager.optimization.objectives as pffun
from portfolio_manager.config.settings import RISK_FREE_RATE
from portfolio_manager.optimization.settings import AllocationStrats
from portfolio_manager.utils.dates import ANN_FACTOR_DICT

logger = logging.getLogger(__name__)

# minimum history of returns of an instrument to be included in an optimization (3 months)
MIN_HISTORY_YEARS = 0.25


class PortfolioOptimizer:
    """Optimal weights of a set of assets from their returns, under the given constraints."""

    def __init__(
        self,
        optimization_type: AllocationStrats,
        returns: pd.DataFrame | None = None,
        min_pf_exposure: float = 0.0,
        max_pf_exposure: float = 1.0,
        bounds_weights: tuple[float, float] | None = None,
        weights_init: np.ndarray | None = None,
        asset_classes: dict[str, str] | None = None,
        class_bounds: dict[str, tuple[float | None, float | None]] | None = None,
        target_vol: float | None = None,
        max_vol: float | None = None,
        risk_budget: dict[str, float] | None = None,
        risk_free_rate: float | None = 0,
        periods_per_year: float = 1.0,
    ):
        self.reset(
            optimization_type=optimization_type,
            returns=returns,
            min_pf_exposure=min_pf_exposure,
            max_pf_exposure=max_pf_exposure,
            bounds_weights=bounds_weights,
            weights_init=weights_init,
            asset_classes=asset_classes,
            class_bounds=class_bounds,
            target_vol=target_vol,
            max_vol=max_vol,
            risk_budget=risk_budget,
            risk_free_rate=risk_free_rate,
            periods_per_year=periods_per_year,
        )

    def reset(
        self,
        optimization_type: AllocationStrats,
        returns: pd.DataFrame | None = None,
        min_pf_exposure: float = 0.0,
        max_pf_exposure: float = 1.0,
        bounds_weights: tuple[float, float] | None = None,
        weights_init: np.ndarray | None = None,
        asset_classes: dict[str, str] | None = None,
        class_bounds: dict[str, tuple[float | None, float | None]] | None = None,
        target_vol: float | None = None,
        max_vol: float | None = None,
        risk_budget: dict[str, float] | None = None,
        risk_free_rate: float | None = 0,
        periods_per_year: float = 1.0,
    ):
        """Set the returns and the constraints of the next optimization. asset_classes: the class of each asset
        (column of the returns); class_bounds: the (min, max) total weight of each class (None: no limit).

        The expected returns and the covariances are annualized with periods_per_year (the number of periods of the
        returns in a year), so that the objectives (return, volatility, Sharpe ratio) and the volatility constraints
        and risk-free rate are annual: daily values (a volatility of about 0.005, a mean return of about 0.0005) are
        too small for the tolerance of the solver, which then often fails.
        """
        self.optimization_type = optimization_type
        self.returns = returns if returns is not None else pd.DataFrame()
        self.min_pf_exposure = min_pf_exposure
        self.max_pf_exposure = max_pf_exposure
        self.returns_clean = self.clean_returns_df(severity='low')
        self.mu = self.returns_clean.mean().values * periods_per_year
        self.cov_mat = self.returns_clean.cov().values * periods_per_year
        self.bounds_weights = bounds_weights
        self.weights_init = weights_init
        self.asset_classes = asset_classes or {}
        self.class_bounds = self.feasible_class_bounds(class_bounds or {})
        self.target_vol = target_vol
        self.max_vol = max_vol
        if risk_budget is not None:
            self.risk_budget = np.array([risk_budget[asset] for asset in self.returns_clean.columns])
        else:
            self.risk_budget = None
        self.optim_fun_args = [self.mu, self.cov_mat, self.risk_budget, risk_free_rate]
        self.weights_curr = None

    def get_constraints(self):
        """Constraints of the solver: exposures, weights, volatility, asset classes."""
        constr_exposure = (
            {'type': 'ineq', 'fun': lambda x: self.max_pf_exposure - np.sum(x)},
            {'type': 'ineq', 'fun': lambda x: np.sum(x) - self.min_pf_exposure},
        )
        if self.bounds_weights is not None:
            constr_bounds_weights = (
                {'type': 'ineq', 'fun': lambda x: x - self.bounds_weights[0]},
                {'type': 'ineq', 'fun': lambda x: self.bounds_weights[1] - x},
            )
        else:
            constr_bounds_weights = ()
        if self.target_vol is not None:
            constr_target_vol = (
                {'type': 'ineq', 'fun': lambda x: 1.05 * self.target_vol - pffun.compute_pf_vol(x, self.cov_mat)},
                {'type': 'ineq', 'fun': lambda x: -0.95 * self.target_vol + pffun.compute_pf_vol(x, self.cov_mat)},
            )
        else:
            constr_target_vol = ()
        if self.max_vol is not None:
            constr_max_vol = ({'type': 'ineq', 'fun': lambda x: self.max_vol - pffun.compute_pf_vol(x, self.cov_mat)},)
        else:
            constr_max_vol = ()
        constr_asset_class = []
        for asset_class, (low, high) in self.class_bounds.items():
            members = self.class_vector(asset_class)
            if low is not None:
                constr_asset_class.append({'type': 'ineq', 'fun': lambda x, v=members, b=low: v.dot(x) - b})
            if high is not None:
                constr_asset_class.append({'type': 'ineq', 'fun': lambda x, v=members, b=high: b - v.dot(x)})
        constr_asset_class = tuple(constr_asset_class)
        return constr_exposure + constr_bounds_weights + constr_target_vol + constr_max_vol + constr_asset_class

    def class_vector(self, asset_class: str) -> np.ndarray:
        """1 for the assets of a class among the assets of the returns, 0 for the others."""
        return np.array([1.0 if self.asset_classes.get(c) == asset_class else 0.0 for c in self.returns_clean.columns])

    def feasible_class_bounds(
        self, class_bounds: dict[str, tuple[float | None, float | None]]
    ) -> dict[str, tuple[float | None, float | None]]:
        """The class bounds the assets of the returns can satisfy: a class without any asset (e.g. none with enough
        history yet) has no bound; a class minimum is lowered to what its assets can hold at their max weight.
        """
        feasible = {}
        max_weight = self.bounds_weights[1] if self.bounds_weights is not None else 1.0
        for asset_class, (low, high) in class_bounds.items():
            n_assets = int(self.class_vector(asset_class).sum())
            if n_assets == 0:
                logger.debug('No asset of class %s available: no bound', asset_class)
                continue
            if low is not None and low > n_assets * max_weight:
                logger.debug('Min weight of class %s lowered to %.0f%%', asset_class, 100 * n_assets * max_weight)
                low = n_assets * max_weight
            feasible[asset_class] = (low, high)
        return feasible

    def _compute_optim_portfolio(self, fun: Callable):
        # SLSQP: suited to linear constraints (exposure, weights, asset classes); on real portfolios with asset class
        # constraints trust-constr failed on some dates and took minutes, SLSQP converged in a fraction of a second
        return minimize(
            fun=fun,
            x0=self.weights_init,
            args=self.optim_fun_args,
            method='SLSQP',
            constraints=self.get_constraints(),
            tol=1e-9,
            options={'maxiter': 1000},
        )

    def clean_returns_df(self, severity: str = 'high') -> pd.DataFrame:
        """Returns without missing values: high drops every asset with gaps, low only empty rows and assets."""
        if severity == 'high':
            returns_clean = self.returns.dropna(axis=1)
        elif severity == 'low':
            returns_clean = self.returns.dropna(axis=0, how='all')
            returns_clean = returns_clean.dropna(axis=1, how='all')
        else:
            raise NotImplementedError
        return returns_clean

    def compute_equal_weights(self) -> pd.Series:
        """The max exposure split equally among the assets, capped at the max weight per asset (rest in cash); with
        class bounds, the target of each class (the middle of its range) split equally among its assets, and the rest
        of the max exposure among the assets of the classes without bounds.
        """
        columns = self.returns_clean.columns
        max_weight = self.bounds_weights[1] if self.bounds_weights is not None else 1.0
        weights = pd.Series(0.0, index=columns, name='weights')
        free = list(columns)
        budget = self.max_pf_exposure
        for asset_class, (low, high) in self.class_bounds.items():
            low, high = low if low is not None else 0.0, high if high is not None else self.max_pf_exposure
            members = [c for c in columns if self.asset_classes.get(c) == asset_class]
            target = (low + high) / 2
            weights[members] = min(target / len(members), max_weight)
            free = [c for c in free if c not in members]
            budget -= target
        if free:
            weights[free] = min(max(budget, 0.0) / len(free), max_weight)
        return weights

    def compute_optimized_portfolio(self) -> pd.Series:
        """Optimized weights, NaN if the optimization fails."""
        try:
            if self.optimization_type == AllocationStrats.EQUAL_WEIGHT:
                return self.compute_equal_weights()  # no optimization needed
            if self.weights_init is None:
                if self.weights_curr is not None:
                    self.weights_init = self.weights_curr.copy()
                else:
                    self.weights_init = np.array([1 / self.returns_clean.shape[1]] * self.returns_clean.shape[1])
            if self.optimization_type == AllocationStrats.MAX_RET:
                optim = self._compute_optim_portfolio(fun=pffun.compute_neg_pf_ret)
            elif self.optimization_type == AllocationStrats.MIN_VAR:
                optim = self._compute_optim_portfolio(fun=pffun.pf_volatility_obj_fun)
            elif self.optimization_type == AllocationStrats.MAX_SHARPE:
                optim = self._compute_optim_portfolio(fun=pffun.neg_pf_sharpe_obj_fun)
            elif self.optimization_type == AllocationStrats.RISK_PARITY:
                optim = self._compute_optim_portfolio(fun=pffun.vol_risk_parity_obj_fun)
            else:
                raise TypeError
            if optim.success:
                logger.debug('Optimized portfolio weights: %s', optim.x)
                if self.optimization_type == AllocationStrats.RISK_PARITY:
                    risk_contrib = pffun.pf_var_contr(optim.x, self.cov_mat) / pffun.compute_pf_vol(
                        optim.x, self.cov_mat
                    )
                    logger.debug('Optimized risk contributions: %s', risk_contrib)
                self.weights_curr = optim.x
                return pd.Series(optim.x, index=self.returns_clean.columns, name='weights')
            else:
                logger.warning(
                    'Optimization not successful (status %s: %s); determinant of the covariance matrix: %s',
                    optim.status,
                    optim.message,
                    np.linalg.det(self.cov_mat),
                )
                return pd.Series(np.nan, index=self.returns_clean.columns, name='weights')
        except (ValueError, ZeroDivisionError) as e:
            if not self.returns_clean.empty:
                logger.warning('Optimization could not be run: %s', e)
            else:
                logger.warning('Optimization could not be run: no returns')
            return pd.Series(np.nan, index=self.returns.columns, name='weights')


class OptimizedWeights(NamedTuple):
    """Weights at each optimization date and the dates where the optimization failed."""

    weights: pd.DataFrame  # one row per optimization date
    failed_dates: list[pd.Timestamp]  # dates where the optimization failed (previous weights kept, if any)


def compute_weights_optim_portfolio(
    allocation_method: AllocationStrats,
    prices: pd.DataFrame,
    sampling_freq: str,
    optimization_freq: str,
    extra_args: dict[str, Any] | None = None,
    start_date: pd.Timestamp | None = None,
    optimize_at_start: bool = False,
) -> OptimizedWeights:
    """Optimizes on each date of optimization_freq from start_date on (all dates if None), and on start_date itself if
    asked (so that a portfolio starting then has weights from its first day), estimating returns and risk from the full
    price history up to that date.
    """
    extra_args = extra_args or {}
    weights_df = pd.DataFrame().reindex_like(prices.resample(optimization_freq).last())
    if start_date is not None:
        weights_df = weights_df[weights_df.index >= start_date]
        if optimize_at_start and start_date not in weights_df.index:
            weights_df = weights_df.reindex(weights_df.index.union([pd.Timestamp(start_date)]))
    args = dict(
        optimization_type=allocation_method,
        periods_per_year=ANN_FACTOR_DICT[sampling_freq],  # annual objectives and volatility constraints
        min_pf_exposure=extra_args.get('min_pf_exposure', 0.0),
        max_pf_exposure=extra_args.get('max_pf_exposure', 1.0),
        bounds_weights=(extra_args.get('min_asset_exposure', 0.0), extra_args.get('max_asset_exposure', 1.0)),
    )
    if extra_args.get('class_bounds'):
        # the class of each asset and the (min, max) weight of each class; the optimizer keeps, on each date, the
        # bounds of the classes with assets available
        args['asset_classes'] = extra_args.get('asset_classes', {})
        args['class_bounds'] = extra_args['class_bounds']
    if allocation_method == AllocationStrats.MAX_RET:
        if 'target_vol' in extra_args:
            args['target_vol'] = extra_args['target_vol']
    elif allocation_method == AllocationStrats.MIN_VAR:
        pass
    elif allocation_method == AllocationStrats.MAX_SHARPE:
        args['risk_free_rate'] = RISK_FREE_RATE
        if 'max_vol' in extra_args:
            args['max_vol'] = extra_args['max_vol']
    elif allocation_method == AllocationStrats.RISK_PARITY:
        if extra_args.get('risk_budget', None) is None:
            args['risk_budget'] = {asset: 1.0 for asset in prices.columns}
        else:
            args['risk_budget'] = extra_args.get('risk_budget', None)
    logger.debug('Optimization frequency %s, settings %s', optimization_freq, args)
    pf_optimizer = PortfolioOptimizer(**args)
    # instruments with a shorter history of returns are left out of the optimization (weight 0)
    min_history = int(round(ANN_FACTOR_DICT[sampling_freq] * MIN_HISTORY_YEARS))
    failed_dates = []
    for idx in weights_df.index:
        logger.debug('Running optimization as of %s', idx)
        returns_df = prices[prices.index <= idx].pct_change()
        too_short = returns_df.columns[returns_df.count() < min_history]
        if len(too_short) > 0:
            logger.debug('Too little history, left out: %s', too_short.to_list())
        args['returns'] = returns_df.drop(columns=too_short)
        pf_optimizer.reset(**args)
        # with few instruments available (e.g. at the start of the history) the minimum invested may be out of
        # reach of the maximum weight per asset: invest as much as allowed
        n_available = pf_optimizer.returns_clean.shape[1]
        pf_optimizer.min_pf_exposure = min(args['min_pf_exposure'], n_available * args['bounds_weights'][1])
        optim_weights = pf_optimizer.compute_optimized_portfolio()
        if optim_weights.isna().all():
            # a failed optimization keeps the previous weights, so that the portfolio keeps a defined target
            failed_dates.append(idx)
            pos = weights_df.index.get_loc(idx)
            if pos > 0:
                logger.warning('Optimization failed as of %s: previous weights kept', idx.date())
                weights_df.iloc[pos, :] = weights_df.iloc[pos - 1, :]
            else:
                logger.warning('Optimization failed as of %s: no previous weights', idx.date())
            continue
        if extra_args.get('max_asset_num', None) is not None:
            optim_weights = optim_weights.mask(
                optim_weights.rank(method='min', ascending=False) > extra_args.get('max_asset_num', None), 0
            )
            logger.debug('Weights of the largest %s assets: %s', extra_args['max_asset_num'], optim_weights.values)
        if extra_args.get('min_position_size', 0.0) > 0:
            optim_weights = apply_min_position_size(
                weights=optim_weights,
                min_size=extra_args['min_position_size'],
                max_weight=extra_args.get('max_asset_exposure', 1.0),
            )
        weights_df.loc[idx, :] = optim_weights
    return OptimizedWeights(weights=weights_df, failed_dates=failed_dates)


def apply_min_position_size(weights: pd.Series, min_size: float, max_weight: float) -> pd.Series:
    """Positions below min_size are dropped; their weight is redistributed proportionally among the remaining positions,
    without exceeding max_weight (what cannot be placed stays in cash).
    """
    if weights.isna().all():
        return weights
    total = weights.sum()
    weights = weights.where(weights >= min_size, 0.0)
    for _ in range(len(weights)):
        free = (weights > 0) & (weights < max_weight)
        missing = total - weights.sum()
        if missing <= 1e-12 or not free.any():
            break
        weights[free] = (weights[free] * (1.0 + missing / weights[free].sum())).clip(upper=max_weight)
    return weights
