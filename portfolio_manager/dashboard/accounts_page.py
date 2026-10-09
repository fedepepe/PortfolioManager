"""Accounts page: the accounts with their state, Connect buttons and the form to add an account. Shown at the first
start-up and while the selected account is not connected.
"""

import json
import logging

import dash_bootstrap_components as dbc
from dash import MATCH, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from portfolio_manager.config.accounts import Account, Brokers, add_account, get_account, has_credentials, list_accounts
from portfolio_manager.degiro.connection import connect_account
from portfolio_manager.storage.queries import query_benchmark
from portfolio_manager.utils.logs import hide_secrets

logger = logging.getLogger(__name__)

# fields of a Degiro credentials file (see README)
DEGIRO_CREDENTIALS_EXAMPLE = {'username': '...', 'password': '...', 'totp_secret_key': '...'}
# widths of the columns of the account list
COLUMNS = [('Account', 2), ('Broker', 1), ('Currency', 1), ('Credentials file', 3), ('Benchmark', 3), ('', 2)]


def build_content_accounts() -> html.Div:
    """Layout of the Accounts page."""
    return html.Div(
        dbc.Card(
            dbc.CardBody(
                [
                    html.H4('Accounts'),
                    html.Div(id='accounts-list', children=accounts_list()),
                    html.Hr(),
                    html.H5('Add an account'),
                    dbc.Row(
                        [
                            dbc.Col(
                                dcc.Dropdown(
                                    [b.value for b in Brokers],
                                    Brokers.DEGIRO.value,
                                    id='dropdown-new-account-broker',
                                    clearable=False,
                                ),
                                width=2,
                            ),
                            dbc.Col(
                                dbc.Input(id='input-new-account-name', placeholder='Portfolio name', type='text'),
                                width=4,
                            ),
                            dbc.Col(dbc.Button('Add account', id='button-add-account', n_clicks=0), width='auto'),
                        ],
                        align='center',
                    ),
                    html.Br(),
                    html.Div(id='add-account-status'),
                ]
            ),
            color='dark',
        )
    )


def accounts_list() -> list:
    """Header and one row per account (each row in its own container, redrawn after a connection)."""
    accounts = list_accounts()
    if not accounts:
        return [html.P('No account yet: add one below.')]
    header = dbc.Row([dbc.Col(html.B(label), width=width) for label, width in COLUMNS], className='mb-2')
    rows = [html.Div(account_row(a), id={'type': 'account-row', 'account': a.name}) for a in accounts]
    return [header, *rows]


def account_row(account: Account, message=None) -> list:
    """Name, broker, currency (or "not connected"), credentials file (found or missing), benchmark and, for an account
    not connected yet, the Connect button; a message (result of the connection) below.
    """
    found = has_credentials(account)
    benchmark = query_benchmark(account.name)
    cells = [
        account.name,
        account.broker.value,
        account.currency or html.Span('not connected', className='text-warning'),
        [
            html.Code(f'credentials/{account.credentials_file}'),
            ' ',
            html.Span('found', className='text-success') if found else html.Span('missing', className='text-danger'),
        ],
        html.Small(benchmark.describe() if benchmark is not None else '-'),
        dbc.Button('Connect', id={'type': 'button-connect', 'account': account.name}, n_clicks=0, size='sm')
        if account.currency is None
        else '',
    ]
    row = dbc.Row([dbc.Col(cell, width=width) for cell, (_, width) in zip(cells, COLUMNS, strict=True)], align='center')
    return [row, message] if message is not None else [row]


def credentials_instructions(account: Account) -> list:
    """What to do after adding an account: where to save its credentials, which fields they need, then Connect."""
    return [
        dbc.Alert(f'Account {account.name} added.', color='success'),
        html.P(
            [
                'Save the Degiro credentials of the account in the file ',
                html.Code(f'credentials/{account.credentials_file}'),
                ' of the project folder (never committed), with these fields (',
                html.Code('totp_secret_key'),
                ' only with two-factor authentication), then press Connect next to the account:',
            ]
        ),
        html.Pre(json.dumps(DEGIRO_CREDENTIALS_EXAMPLE, indent=2)),
    ]


@callback(
    Output('add-account-status', 'children'),
    Output('accounts-list', 'children'),
    Output('store-account', 'data', allow_duplicate=True),
    Input('button-add-account', 'n_clicks'),
    State('dropdown-new-account-broker', 'value'),
    State('input-new-account-name', 'value'),
    prevent_initial_call=True,
)
def run_add_account(n_clicks, broker: str, name: str | None) -> tuple:
    """Add the account (Add account button), list it and say where its credentials go; show the error if it cannot
    be added.
    """
    # the button is inserted with the page: act only on an actual click
    if not n_clicks:
        raise PreventUpdate
    try:
        account = add_account(name=name or '', broker=Brokers(broker))
    except ValueError as e:
        return dbc.Alert(str(e), color='danger'), no_update, no_update
    return credentials_instructions(account), accounts_list(), account.name


@callback(
    Output({'type': 'account-row', 'account': MATCH}, 'children'),
    Input({'type': 'button-connect', 'account': MATCH}, 'n_clicks'),
    prevent_initial_call=True,
)
def run_connect(n_clicks) -> list:
    """Connect the account (Connect button): save its currency and the currency pairs, give it the default benchmark;
    show the result, or why it failed, below its row.
    """
    if not n_clicks:
        raise PreventUpdate
    account = get_account(ctx.triggered_id['account'])
    if not has_credentials(account):
        message = f'credentials/{account.credentials_file} not found: save the credentials of the account there first'
        return account_row(account, dbc.Alert(message, color='danger'))
    try:
        account = connect_account(account)
    except Exception as e:  # any login or download problem: shown below the account, details in the log
        logger.exception('Connection of %s failed', account.name)
        return account_row(account, dbc.Alert(f'Not connected: {hide_secrets(str(e))}', color='danger'))
    benchmark = query_benchmark(account.name)
    text = f'Connected: base currency {account.currency}. ' + (
        f'Benchmark: {benchmark.describe()}. '
        if benchmark is not None
        else f'There is no default benchmark for {account.currency}. '
    )
    return account_row(account, dbc.Alert(text + 'Next: press Update on the Portfolio page.', color='success'))
