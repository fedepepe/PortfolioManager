import dash_bootstrap_components as dbc
from dash import Dash, html, dcc

from dashboard.dash_common import CONTENT_STYLE
from dashboard.dash_sidebar import sidebar

app = Dash(
    __name__,
    requests_pathname_prefix='/portfolio_manager/',
    external_stylesheets=[dbc.themes.SLATE],
    meta_tags=[{'name': 'portfolio', 'content': 'width=device-width'}],
)

# Content
content = html.Div(id='page-content', style=CONTENT_STYLE)

# App Layout
app.layout = dbc.Container(
    html.Div(
        [
            # selected account, kept per browser
            dcc.Store(id='store-account', storage_type='local'),
            # changed by an update of the data, triggers a redraw of the figures
            dcc.Store(id='store-data-version'),
            # changed by a new optimization, triggers a redraw of the strategies figures
            dcc.Store(id='store-strategy-version'),
            sidebar,
            content,
        ]
    ),
    fluid=True,
    className='dashboard-container',
)
