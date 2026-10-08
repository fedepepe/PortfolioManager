import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from dash import dcc

from engines.reporting import Metrics, to_str_risk_metrics
from utils.file_utils import PD_DATA_TYPES

# Styling
SIDEBAR_STYLE = {
    'position': 'fixed',
    'top': 0,
    # "left": 0,
    'bottom': 0,
    'width': '10rem',
    'padding': '2rem 0rem',
}
CONTENT_STYLE = {
    'margin-left': '12rem',
    # "margin-right": "2rem",
    'padding': '2rem 0rem',
}

LAYOUT_TEMPLATE = 'plotly_dark'

COLOR_ROW_EVEN = px.colors.qualitative.Plotly[2]
COLOR_ROW_ODD = px.colors.qualitative.Plotly[0]


def loading_wrapper(children) -> dcc.Loading:
    return dcc.Loading(type='default', children=children)


def card_wrapper(children) -> dbc.Card:
    return dbc.Card(children=children, body=True, color=pio.templates['plotly_dark'].layout.plot_bgcolor)


def get_fig_empty() -> go.Figure:
    return go.Figure(layout=go.Layout(template=LAYOUT_TEMPLATE))


# PERFORMANCE METRICS TABLE: one column per entry of risk_metrics_dct (label -> risk metrics, None if missing)
def get_fig_metrics_table(risk_metrics_dct: dict[str, PD_DATA_TYPES | None]) -> go.Figure:
    columns = [
        to_str_risk_metrics(m) if m is not None else pd.Series(name='Parameter', dtype=str)
        for m in risk_metrics_dct.values()
    ]
    perf_df = pd.concat(columns, axis=1)
    perf_df = perf_df.drop([Metrics.BETA_OVERALL.name, Metrics.SKEWNESS.name], errors='ignore')
    return go.Figure(
        data=[
            go.Table(
                columnwidth=[120, 50],
                header=dict(
                    values=['<b>Performance Metric</b>'] + [f'<b>{label}</b>' for label in risk_metrics_dct],
                    align=['left', 'center'],
                    height=25,
                ),
                cells=dict(
                    values=perf_df.round(3).replace(np.nan, '').reset_index().T.values.tolist(),
                    # 2-D list of colors for alternating rows
                    fill_color=[[COLOR_ROW_ODD, COLOR_ROW_EVEN] * len(perf_df)],
                    align=['left', 'center'],
                    height=22,
                ),
            )
        ],
        layout=go.Layout(template=LAYOUT_TEMPLATE, margin={'l': 30, 'r': 30, 't': 30, 'b': 30}),
    )


def compute_corr_mat(df) -> pd.DataFrame:
    corr_mat = df.corr()
    corr_mat = np.tril(corr_mat)
    corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
    corr_df = pd.DataFrame(corr_mat, columns=df.columns, index=df.columns)
    corr_df = corr_df.loc[list(reversed(df.columns)), :]
    return corr_df
