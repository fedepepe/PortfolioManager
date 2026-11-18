import logging

import pytest

import portfolio_manager.dashboard.data_service as data_service
import tasks
from portfolio_manager.config.accounts import Account, Brokers, add_account, list_accounts


@pytest.fixture
def no_db(temp_db, monkeypatch):
    # two accounts in a temporary database, already initialized
    add_account('Portfolio CHF', Brokers.DEGIRO, currency='CHF')
    add_account('Portfolio EUR', Brokers.DEGIRO, currency='EUR')
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
    assert updated == list_accounts() and len(updated) == 2


def test_unknown_account_is_rejected(no_db):
    with pytest.raises(SystemExit, match='Portfolio CHF'):
        tasks.main(['show-benchmark', '--account', 'unknown'])


def test_add_account(no_db):
    tasks.main(['add-account', '--name', 'Portfolio USD', '--currency', 'USD'])
    assert list_accounts()[-1] == Account('Portfolio USD', Brokers.DEGIRO, 'portfolio_usd.json', 'USD')


def test_command_is_required():
    with pytest.raises(SystemExit):
        tasks.build_parser().parse_args([])


def test_etf_performance_requires_isin():
    parser = tasks.build_parser()
    assert parser.parse_args(['etf-performance', '--isin', 'IE00B4L5Y983']).isin == 'IE00B4L5Y983'
    with pytest.raises(SystemExit):
        parser.parse_args(['etf-performance'])


def test_show_benchmark_of_an_account_without_one(no_db, caplog):
    caplog.set_level(logging.INFO)
    tasks.main(['add-account', '--name', 'Portfolio USD', '--currency', 'USD'])
    tasks.main(['show-benchmark'])
    messages = [r.getMessage() for r in caplog.records]
    assert 'Portfolio USD has no benchmark yet' in messages
    assert any(m.startswith('Benchmark of Portfolio CHF: 60% IWDC') for m in messages)
