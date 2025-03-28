import dash_bootstrap_components as dbc
from dash import Dash, html, Input, Output, callback, ctx

from dash_common import CONTENT_STYLE
from dash_sidebar import sidebar
from dash_portfolio import content_portfolio
from dash_instruments import content_instruments

dash = Dash(__name__,
            requests_pathname_prefix="/portfolio_manager/",
            external_stylesheets=[dbc.themes.SLATE],
            meta_tags=[{"name": "portfolio", "content": "width=device-width"}])

# Content
content = html.Div(id="page-content", style=CONTENT_STYLE)

# App Layout
dash.layout = dbc.Container(
    html.Div(
        [
            sidebar,
            content,
        ]
    ),
    fluid=True,
    # style={'display': 'flex'},
    className='dashboard-container'
)


@callback(Output("page-content", "children"),
          Input("button-portfolio", "n_clicks"),
          Input("button-instruments", "n_clicks"),
          Input("button-strategies", "n_clicks"),
          )
def switch_content(n1, n2, n3):
    if ctx.triggered_id == "button-portfolio":
        return content_portfolio
    elif ctx.triggered_id == "button-instruments":
        return content_instruments
    else:
        return content_portfolio
