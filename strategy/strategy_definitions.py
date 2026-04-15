from enum import Enum
from engines.reporting import compute_portfolio_metrics


class AllocationStrats(Enum):
	MAX_RET = 'max_ret'
	MIN_VAR = 'min_var'
	MAX_SHARPE = 'max_sharpe'
	RISK_PARITY = 'risk_parity'


# class StrategyData:
# 	def __init__(self, hist_data: HistPortfolioData):
# 		self.hist_data = hist_data
# 		self.perf_dct = compute_portfolio_metrics(nav=hist_data.nav_eff)
