import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
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

LAYOUT_TEMPLATE = "plotly_dark"

COLOR_ROW_EVEN = px.colors.qualitative.Plotly[2]
COLOR_ROW_ODD = px.colors.qualitative.Plotly[0]


def loading_wrapper(children) -> dcc.Loading:
    return dcc.Loading(type="default", children=children)


def card_wrapper(children) -> dbc.Card:
    return dbc.Card(children=children, body=True, color=pio.templates["plotly_dark"].layout.plot_bgcolor)


def compute_corr_mat(df) -> pd.DataFrame:
    corr_mat = df.corr()
    corr_mat = np.tril(corr_mat)
    corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
    corr_df = pd.DataFrame(corr_mat, columns=df.columns, index=df.columns)
    corr_df = corr_df.loc[list(reversed(df.columns)), :]
    return corr_df
