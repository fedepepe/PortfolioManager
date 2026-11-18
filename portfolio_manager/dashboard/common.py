"""Components and figures shared by the pages."""

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from dash import dcc, html

from portfolio_manager.analytics.metrics import Metrics, to_str_risk_metrics
from portfolio_manager.storage.files import PD_DATA_TYPES

# Styling
SIDEBAR_STYLE = {
    'position': 'fixed',
    'top': 0,
    'bottom': 0,
    'width': '10rem',
    'padding': '2rem 0rem',
}
CONTENT_STYLE = {
    'margin-left': '12rem',
    'padding': '2rem 0rem',
}

LAYOUT_TEMPLATE = 'plotly_dark'

COLOR_ROW_EVEN = px.colors.qualitative.Plotly[2]
COLOR_ROW_ODD = px.colors.qualitative.Plotly[0]


def loading_wrapper(children) -> dcc.Loading:
    """Show a spinner while the children are loading."""
    return dcc.Loading(type='default', children=children)


def card_wrapper(children) -> dbc.Card:
    """Card with the background color of the figures."""
    return dbc.Card(children=children, body=True, color=pio.templates['plotly_dark'].layout.plot_bgcolor)


def get_fig_empty() -> go.Figure:
    """Empty figure, shown until the real one is drawn."""
    return go.Figure(layout=go.Layout(template=LAYOUT_TEMPLATE))


# PERFORMANCE METRICS TABLE: one column per entry of risk_metrics_dct (label -> risk metrics, None if missing)
def header_label(label: str) -> list:
    """A column title, its lines split where it has <br>."""
    lines = label.split('<br>')
    parts = []
    for n, line in enumerate(lines):
        parts += [line] if n == 0 else [html.Br(), line]
    return parts


# PERFORMANCE METRICS TABLE: one column per entry of risk_metrics_dct (label -> risk metrics, None if missing)
def metrics_table(risk_metrics_dct: dict[str, PD_DATA_TYPES | None]) -> html.Table:
    """Table of performance metrics, one column per entry (None: empty column): a table of the page (assets/
    tables.css), as tall as its rows, its text centred vertically, the rows in alternating colours.
    """
    columns = [
        to_str_risk_metrics(m) if m is not None else pd.Series(name='Parameter', dtype=str)
        for m in risk_metrics_dct.values()
    ]
    perf_df = pd.concat(columns, axis=1)
    perf_df = perf_df.drop([Metrics.BETA_OVERALL.name, Metrics.SKEWNESS.name], errors='ignore')
    values = perf_df.round(3).replace(np.nan, '')
    header = html.Tr([html.Th('Performance Metric')] + [html.Th(header_label(label)) for label in risk_metrics_dct])
    rows = [
        html.Tr(
            [html.Td(metric)] + [html.Td(value) for value in row],
            style={'backgroundColor': COLOR_ROW_ODD if n % 2 == 0 else COLOR_ROW_EVEN},
        )
        for n, (metric, row) in enumerate(values.iterrows())
    ]
    return html.Table([html.Thead(header), html.Tbody(rows)], className='metrics-table')


def compute_corr_mat(df) -> pd.DataFrame:
    """Correlation matrix of the columns of df, lower triangle only, rows in reverse order."""
    corr_mat = df.corr()
    corr_mat = np.tril(corr_mat)
    corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
    corr_df = pd.DataFrame(corr_mat, columns=df.columns, index=df.columns)
    corr_df = corr_df.loc[list(reversed(df.columns)), :]
    return corr_df
