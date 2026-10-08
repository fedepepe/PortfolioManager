import pytest

import portfolio_manager.dashboard.data_service as data_service
import tasks
from portfolio_manager.config.accounts import Accounts


@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(tasks, 'init_db', lambda: None)


def test_every_command_has_help():
    parser = tasks.build_parser()
    for name, (function, _) in tasks.COMMANDS.items():
        assert function.__doc__, name
    assert parser.parse_args(['update']).account is None


def test_update_all_accounts(no_db, monkeypatch):
    updated = []
    monkeypatch.setattr(data_service, 'update_account', updated.append)
    tasks.main(['update'])
    assert updated == list(Accounts)


def test_optimize_one_account_with_saved_settings(no_db, monkeypatch):
    calls = []
    monkeypatch.setattr(data_service, 'get_optimized_data', lambda account: None)
    monkeypatch.setattr(data_service, 'run_optimization', lambda account, settings: calls.append(account) or 'ok')
    tasks.main(['optimize', '--account', 'degiro_eur'])
    assert calls == [Accounts.DEGIRO_EUR]


def test_unknown_account_is_rejected():
    with pytest.raises(SystemExit):
        tasks.build_parser().parse_args(['optimize', '--account', 'unknown'])


def test_command_is_required():
    with pytest.raises(SystemExit):
        tasks.build_parser().parse_args([])


def test_etf_performance_requires_isin():
    parser = tasks.build_parser()
    assert parser.parse_args(['etf-performance', '--isin', 'IE00B4L5Y983']).isin == 'IE00B4L5Y983'
    with pytest.raises(SystemExit):
        parser.parse_args(['etf-performance'])
