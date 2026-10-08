import importlib
import os
import pkgutil

import pytest

import portfolio_manager


def project_modules() -> list[str]:
    modules = ['main', 'tasks']
    modules += [m.name for m in pkgutil.walk_packages(portfolio_manager.__path__, prefix='portfolio_manager.')]
    return modules


@pytest.mark.parametrize('module', project_modules())
def test_module_imports(module):
    # catches broken imports, e.g. a name imported from a module that no longer provides it
    importlib.import_module(module)


def test_project_folders():
    from portfolio_manager.config.settings import DATA_DIR, PROJECT_DIR

    # data, results, state and credentials stay at the project root
    assert os.path.isfile(os.path.join(PROJECT_DIR, 'main.py'))
    assert DATA_DIR == os.path.join(PROJECT_DIR, 'data')
