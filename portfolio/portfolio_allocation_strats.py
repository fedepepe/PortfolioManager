from enum import Enum


class PortfolioAllocationStrats(Enum):
	MAX_RET = 'max_ret'
	MIN_VAR = 'min_var'
	MAX_SHARPE = 'max_sharpe'
	RISK_PARITY = 'risk_parity'
