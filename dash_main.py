import dash_bootstrap_components as dbc
from dash import Dash, html

from dash_common import CONTENT_STYLE
from dash_sidebar import sidebar

app = Dash(__name__,
           requests_pathname_prefix="/portfolio_manager/",
           external_stylesheets=[dbc.themes.SLATE],
           meta_tags=[{"name": "portfolio", "content": "width=device-width"}])

# Content
content = html.Div(id="page-content", style=CONTENT_STYLE)

# App Layout
app.layout = dbc.Container(
    html.Div(
        [
            sidebar,
            content,
        ]
    ),
    fluid=True,
    className='dashboard-container'
)
