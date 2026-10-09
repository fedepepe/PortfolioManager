import json

import pytest

from portfolio_manager.config.accounts import Account, Brokers, list_accounts
from portfolio_manager.dashboard.app import app


@pytest.fixture
def client(temp_db):
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
