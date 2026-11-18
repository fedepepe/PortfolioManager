"""Sidebar and page switching."""

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, ctx, html

from portfolio_manager.config.accounts import default_account, get_account
from portfolio_manager.dashboard.accounts_page import build_content_accounts
from portfolio_manager.dashboard.common import SIDEBAR_STYLE
from portfolio_manager.dashboard.markets_page import build_content_markets
from portfolio_manager.dashboard.portfolio_page import build_content_portfolio
from portfolio_manager.dashboard.saa_page import build_content_saa

# Sidebar
sidebar = html.Div(
    dbc.Card(
        dbc.CardBody(
            dbc.Row(
                [
                    dbc.ButtonGroup(
                        [
                            dbc.Button('Portfolio', id='button-portfolio'),
                            html.Br(),
                            dbc.Button('Markets', id='button-markets'),
                            html.Br(),
                            dbc.Button('Strategic Asset Allocation', id='button-saa'),
                            html.Br(),
                            dbc.Button('Accounts', id='button-accounts'),
                        ],
                        vertical=True,
                    )
                ],
                align='center',
            )
        ),
        color='dark',
    ),
    style=SIDEBAR_STYLE,
)


@callback(
    Output('page-content', 'children'),
    Input('button-portfolio', 'n_clicks'),
    Input('button-markets', 'n_clicks'),
    Input('button-saa', 'n_clicks'),
    Input('button-accounts', 'n_clicks'),
    State('store-account', 'data'),
)
def switch_content(n1, n2, n3, n4, account_name):
    """Show the page of the clicked sidebar button for the remembered account; the Accounts page without accounts,
    and at start-up while the account is not connected.
    """
    account = get_account(account_name) or default_account()
    starting = ctx.triggered_id is None
    if account is None or ctx.triggered_id == 'button-accounts' or (starting and account.currency is None):
        return build_content_accounts()
    if ctx.triggered_id == 'button-markets':
        return build_content_markets()
    elif ctx.triggered_id == 'button-saa':
        return build_content_saa(account=account)
    else:
        return build_content_portfolio(account=account)
