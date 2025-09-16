import dash_bootstrap_components as dbc
from dash import html, callback, Output, Input, ctx

from dashboard.dash_common import SIDEBAR_STYLE
from dashboard.dash_instruments import build_content_instruments
from dashboard.dash_portfolio import build_content_portfolio
from dash_strategies import build_content_strategies
from definitions import Accounts

# Sidebar
sidebar = html.Div(
    dbc.Card(
        dbc.CardBody(
            dbc.ButtonGroup([
                dbc.Button("Portfolio", id="button-portfolio"),
                html.Br(),
                dbc.Button("Instruments", id="button-instruments"),
                html.Br(),
                dbc.Button("Strategies", id="button-strategies"),
            ],
                vertical=True,
            )
        ), color='dark'),
    style=SIDEBAR_STYLE
)

account = Accounts.CHF


@callback(Output("page-content", "children"),
          Input("button-portfolio", "n_clicks"),
          Input("button-instruments", "n_clicks"),
          Input("button-strategies", "n_clicks"),
          )
def switch_content(n1, n2, n3):
    if ctx.triggered_id == "button-portfolio":
        return build_content_portfolio(account=account)
    elif ctx.triggered_id == "button-instruments":
        return build_content_instruments()
    elif ctx.triggered_id == "button-strategies":
        return build_content_strategies(account=account)
    else:
        return build_content_portfolio(account=account)
