import dash_bootstrap_components as dbc
import plotly.io as pio
from dash import dcc

# Styling
SIDEBAR_STYLE = {
    "position": "fixed",
    "top": 0,
    # "left": 0,
    "bottom": 0,
    "width": "10rem",
    "padding": "2rem 0rem",
}
CONTENT_STYLE = {
    "margin-left": "12rem",
    # "margin-right": "2rem",
    "padding": "2rem 0rem",
}


def loading_wrapper(children) -> dcc.Loading:
    return dcc.Loading(type="default", children=children)


def card_wrapper(children) -> dbc.Card:
    return dbc.Card(children=children, body=True, color=pio.templates["plotly_dark"].layout.plot_bgcolor)
