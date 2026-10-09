import json

import pytest

import portfolio_manager.analytics.performance as performance
import portfolio_manager.backtest.workflows as workflows
import portfolio_manager.dashboard.accounts_page as accounts_page
import portfolio_manager.dashboard.portfolio_page as portfolio_page
import portfolio_manager.degiro.connection as connection
import portfolio_manager.storage.tables as tables
from portfolio_manager.config.accounts import Account, Brokers, add_account, get_account, list_accounts
from portfolio_manager.dashboard.app import app
from tests.test_connection import FakeConnection


@pytest.fixture
def client(temp_db, tmp_path, monkeypatch):
    # temporary database, data, results and Parquet folders: no saved data of the project is read
    monkeypatch.setattr(workflows, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(performance, 'RESULTS_DIR', str(tmp_path / 'results'))
    monkeypatch.setattr(tables, 'DERIVED_DIR', str(tmp_path / 'data' / 'derived'))
    return app.server.test_client()


def call(client, output: str, inputs: dict, states: dict | None = None) -> dict:
    # one callback, as the browser calls it: {component id.property: value}
    deps = client.get('/_dash-dependencies').get_json()
    dep = next(d for d in deps if output in d['output'])
    outputs = [o.split('.') for o in dep['output'].strip('.').split('...')]
    payload = {
        'output': dep['output'],
        'outputs': [{'id': i, 'property': p.split('@')[0]} for i, p in outputs]
        if len(outputs) > 1
        else {'id': outputs[0][0], 'property': outputs[0][1]},
        'inputs': [dict(i, value=inputs.get(f'{i["id"]}.{i["property"]}')) for i in dep['inputs']],
        'state': [dict(s, value=(states or {}).get(f'{s["id"]}.{s["property"]}')) for s in dep.get('state', [])],
        'changedPropIds': list(inputs),
    }
    response = client.post('/_dash-update-component', data=json.dumps(payload), content_type='application/json')
    assert response.status_code == 200, response.data[:500]
    return response.get_json()['response']


def test_without_accounts_the_page_asks_to_add_one(client):
    page = json.dumps(call(client, 'page-content.children', {'button-portfolio.n_clicks': 1}))
    assert 'dropdown-new-account-broker' in page and 'input-new-account-name' in page and 'Add account' in page


def add(client, name):
    return call(
        client,
        'add-account-status.children',
        {'button-add-account.n_clicks': 1},
        {'dropdown-new-account-broker.value': 'Degiro', 'input-new-account-name.value': name},
    )


def test_added_account_and_where_its_credentials_go(client):
    response = add(client, 'Portfolio USD')
    assert 'credentials/portfolio_usd.json' in json.dumps(response['add-account-status'])
    assert response['store-account']['data'] == 'Portfolio USD'
    assert list_accounts() == [Account('Portfolio USD', Brokers.DEGIRO, 'portfolio_usd.json')]


@pytest.mark.parametrize('name', ['', 'Portfolio USD'])
def test_account_that_cannot_be_added_shows_the_error(client, name):
    add(client, 'Portfolio USD')
    response = add(client, name)
    assert 'danger' in json.dumps(response['add-account-status'])
    assert 'store-account' not in response  # the selected account is kept
    assert len(list_accounts()) == 1


def test_added_account_is_listed_with_a_connect_button(client):
    response = add(client, 'Portfolio USD')
    listed = json.dumps(response['accounts-list'])
    assert 'Portfolio USD' in listed and 'not connected' in listed and 'button-connect' in listed
    assert 'missing' in listed  # no credentials file yet


def connect(client, account_name: str) -> str:
    # the Connect button of one account (pattern-matching ids), as the browser calls it
    deps = client.get('/_dash-dependencies').get_json()
    dep = next(d for d in deps if 'account-row' in d['output'])
    button = {'type': 'button-connect', 'account': account_name}
    payload = {
        'output': dep['output'],
        'outputs': {'id': {'type': 'account-row', 'account': account_name}, 'property': 'children'},
        'inputs': [{'id': button, 'property': 'n_clicks', 'value': 1}],
        'state': [],
        'changedPropIds': [json.dumps(button, sort_keys=True, separators=(',', ':')) + '.n_clicks'],
    }
    response = client.post('/_dash-update-component', data=json.dumps(payload), content_type='application/json')
    assert response.status_code == 200, response.data[:500]
    return json.dumps(response.get_json()['response'])


@pytest.fixture
def credentials_found(monkeypatch):
    monkeypatch.setattr(accounts_page, 'has_credentials', lambda account: True)


def test_connect_saves_the_currency_and_shows_the_benchmark(client, credentials_found, monkeypatch):
    monkeypatch.setattr(
        accounts_page, 'connect_account', lambda account: connection.connect_account(account, conn=FakeConnection())
    )
    add_account('Portfolio CHF', Brokers.DEGIRO)
    row = connect(client, 'Portfolio CHF')
    assert 'Connected: base currency CHF' in row and '60% IWDC' in row and 'press Update' in row
    assert get_account('Portfolio CHF').currency == 'CHF'
    assert 'button-connect' not in row  # connected: no button any more


def test_connect_without_credentials_file(client):
    add_account('Portfolio CHF', Brokers.DEGIRO)
    row = connect(client, 'Portfolio CHF')
    assert 'credentials/portfolio_chf.json not found' in row and get_account('Portfolio CHF').currency is None


def test_failed_connection_shows_the_error_without_secrets(client, credentials_found, monkeypatch):
    def failing(account):
        raise ConnectionError('400 for url: https://trader.degiro.nl/x;jsessionid=SECRET1.p_2')

    monkeypatch.setattr(accounts_page, 'connect_account', failing)
    add_account('Portfolio CHF', Brokers.DEGIRO)
    row = connect(client, 'Portfolio CHF')
    assert 'Not connected' in row and 'jsessionid=<hidden>' in row and 'SECRET1' not in row


def test_start_up_shows_the_accounts_page_while_the_account_is_not_connected(client):
    add_account('Portfolio CHF', Brokers.DEGIRO)
    assert 'accounts-list' in json.dumps(call(client, 'page-content.children', {}))
    # the Portfolio button shows the Portfolio page, with a message
    assert 'dropdown-portfolio' in json.dumps(call(client, 'page-content.children', {'button-portfolio.n_clicks': 1}))


def render(client, output: str, dropdown: str, account_name: str) -> dict:
    return call(client, output, {f'{dropdown}.value': account_name})


@pytest.mark.parametrize(
    ('currency', 'message', 'update_disabled'),
    [(None, 'is not connected yet', True), ('CHF', 'No data yet for Portfolio CHF: press Update', False)],
)
def test_portfolio_and_strategies_pages_without_data(client, currency, message, update_disabled):
    add_account('Portfolio CHF', Brokers.DEGIRO, currency=currency)
    portfolio = render(client, 'fig_navs.figure', 'dropdown-portfolio', 'Portfolio CHF')
    assert message in json.dumps(portfolio['portfolio-message'])
    assert portfolio['portfolio-figures']['style'] == {'display': 'none'}
    assert portfolio['button-update']['disabled'] is update_disabled
    strategies = render(client, 'fig_strat_navs.figure', 'dropdown-strategies', 'Portfolio CHF')
    expected = message if currency is None else 'press Update on the Portfolio page'
    assert expected in json.dumps(strategies['strategies-message'])
    assert strategies['button-optimize']['disabled'] is True


def test_failed_update_is_shown_next_to_the_button(client, monkeypatch):
    def failing(account):
        raise FileNotFoundError('credentials/portfolio_chf.json')

    monkeypatch.setattr(portfolio_page, 'update_account', failing)
    add_account('Portfolio CHF', Brokers.DEGIRO, currency='CHF')
    response = call(
        client, 'update-status.children', {'button-update.n_clicks': 1}, {'dropdown-portfolio.value': 'Portfolio CHF'}
    )
    assert 'Not updated: credentials/portfolio_chf.json' in json.dumps(response['update-status'])
    assert 'store-data-version' not in response  # the figures are not redrawn
