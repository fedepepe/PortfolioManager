"""Add-account page: shown at the first start-up, when there is no account yet."""

import json

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate

from portfolio_manager.config.accounts import Brokers, add_account

# fields of a Degiro credentials file (see README)
DEGIRO_CREDENTIALS_EXAMPLE = {'username': '...', 'password': '...', 'totp_secret_key': '...'}


def build_content_add_account() -> html.Div:
    """Layout of the add-account page: broker, portfolio name and the Add account button."""
    return html.Div(
        dbc.Card(
            dbc.CardBody(
                [
                    html.H4('Add an account'),
                    html.Br(),
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


def credentials_instructions(account_name: str, credentials_file: str) -> list:
    """What to do after adding an account: where to save its credentials and which fields they need."""
    return [
        dbc.Alert(f'Account {account_name} added.', color='success'),
        html.P(
            [
                'Save the Degiro credentials of the account in the file ',
                html.Code(f'credentials/{credentials_file}'),
                ' of the project folder (never committed), with these fields (',
                html.Code('totp_secret_key'),
                ' only with two-factor authentication):',
            ]
        ),
        html.Pre(json.dumps(DEGIRO_CREDENTIALS_EXAMPLE, indent=2)),
    ]


@callback(
    Output('add-account-status', 'children'),
    Output('store-account', 'data', allow_duplicate=True),
    Input('button-add-account', 'n_clicks'),
    State('dropdown-new-account-broker', 'value'),
    State('input-new-account-name', 'value'),
    prevent_initial_call=True,
)
def run_add_account(n_clicks, broker: str, name: str | None) -> tuple:
    """Add the account (Add account button) and say where its credentials go; show the error if it cannot be added."""
    # the button is inserted with the page: act only on an actual click
    if not n_clicks:
        raise PreventUpdate
    try:
        account = add_account(name=name or '', broker=Brokers(broker))
    except ValueError as e:
        return dbc.Alert(str(e), color='danger'), no_update
    return credentials_instructions(account.name, account.credentials_file), account.name
