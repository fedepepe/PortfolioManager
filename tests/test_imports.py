import importlib
import pkgutil

import pytest

PACKAGES = ['config', 'dashboard', 'database', 'degiro', 'engines', 'portfolio', 'strategy', 'utils', 'yahoo_finance']
# work in progress of the author, not part of the project yet
EXCLUDED = {'engines.instrument_price_forecasting', 'engines.strategy_simple'}


def project_modules() -> list[str]:
    modules = ['main', 'tasks']
    for package in PACKAGES:
        path = importlib.import_module(package).__path__
        modules += [f'{package}.{m.name}' for m in pkgutil.iter_modules(path) if f'{package}.{m.name}' not in EXCLUDED]
    return modules


@pytest.mark.parametrize('module', project_modules())
def test_module_imports(module):
    # catches broken imports, e.g. a name imported from a module that no longer provides it
    importlib.import_module(module)
