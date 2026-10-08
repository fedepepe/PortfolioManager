"""Sidebar and page switching."""

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, ctx, html

from portfolio_manager.config.accounts import Accounts
from portfolio_manager.dashboard.common import SIDEBAR_STYLE
from portfolio_manager.dashboard.instruments_page import build_content_instruments
from portfolio_manager.dashboard.portfolio_page import build_content_portfolio
from portfolio_manager.dashboard.strategies_page import build_content_strategies

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
                            dbc.Button('Instruments', id='button-instruments'),
                            html.Br(),
                            dbc.Button('Strategies', id='button-strategies'),
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
    Input('button-instruments', 'n_clicks'),
    Input('button-strategies', 'n_clicks'),
    State('store-account', 'data'),
)
def switch_content(n1, n2, n3, account_name):
    """Show the page of the clicked sidebar button for the remembered account."""
    account = Accounts.get_account_by_name(name=account_name) or Accounts.get_default_account()
    if ctx.triggered_id == 'button-instruments':
        return build_content_instruments(account=account)
    elif ctx.triggered_id == 'button-strategies':
        return build_content_strategies(account=account)
    else:
        return build_content_portfolio(account=account)
