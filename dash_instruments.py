import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, html, dcc

from instruments_performance import load_etf_catalog_data
from instruments_performance import load_etf_catalog_performance, compute_etf_catalog_performance
from reporting import Metrics
from yfinance_api import YFinHistCols

MAX_INSTR_CORR = 50

dash = Dash(__name__,
            requests_pathname_prefix="/instruments/",
            external_stylesheets=[dbc.themes.SLATE],
            meta_tags=[{"name": "portfolio", "content": "width=device-width"}])


# LOAD DATA
class InstrumentsData:
    def __init__(self):
        self.perf_df = load_etf_catalog_performance()
        adj_close_df = load_etf_catalog_data(column=YFinHistCols.adj_close)
        self.adj_close_df = adj_close_df.loc[:, ~adj_close_df.columns.duplicated()].copy()

    def update(self):
        compute_etf_catalog_performance()
        self.__init__()


instr_data = InstrumentsData()
cols_perf_table = [Metrics.PA_RETURN,
                   Metrics.LAST_YEAR_RETURN,
                   Metrics.ANN_3Y_RETURN,
                   Metrics.ANN_5Y_RETURN,
                   Metrics.VOLATILITY,
                   Metrics.SHARPE_RATIO,
                   Metrics.SORTINO_RATIO,
                   Metrics.MAX_DD]
labels_perf_table_ext = ['Ticker'] + [m.name for m in cols_perf_table]
column_defs = [{"field": m.name,
                "filter": "agNumberColumnFilter",
                "valueFormatter": {"function": m.to_ag_grid_format_func()}
                } for m in cols_perf_table]
column_defs = [{"field": 'Ticker'}] + column_defs


# PERFORMANCE METRICS TABLE
def get_table_perf() -> dag.AgGrid:
    return dag.AgGrid(
        id="table_perf",
        className='ag-theme-alpine-dark',
        columnDefs=column_defs,
        rowData=instr_data.perf_df.reset_index()[labels_perf_table_ext].to_dict("records"),
        columnSize="responsiveSizeToFit",
        defaultColDef={"filter": "agTextColumnFilter"},
        dashGridOptions={"animateRows": False}
    )


# INSTRUMENTS CORRELATION MATRIX HEATMAP
def get_fig_corr() -> go.Figure:
    corr_mat = instr_data.adj_close_df.resample('W-WED').last().pct_change().corr()
    corr_mat = np.tril(corr_mat)
    corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
    corr_mat = pd.DataFrame(corr_mat, columns=instr_data.adj_close_df.columns,
                            index=instr_data.adj_close_df.columns)
    corr_mat = corr_mat.loc[list(reversed(instr_data.adj_close_df.columns)), :].values
    return go.Figure(data=[go.Heatmap(z=corr_mat,
                                      x=instr_data.adj_close_df.columns,
                                      y=list(reversed(instr_data.adj_close_df.columns)),
                                      colorscale='RdBu_r',
                                      zmin=-1,
                                      zmax=1,
                                      xgap=1,
                                      ygap=1,
                                      hoverongaps=False)],
                     layout=go.Layout(title=dict(text="Instruments correlation matrix"),
                                      template="plotly_dark",
                                      xaxis=dict(side='top', scaleanchor="y", constrain="domain"),
                                      yaxis=dict(scaleanchor="x", constrain="domain"),
                                      )
                     )


# DASHBOARD
content_instruments = html.Div([
    get_table_perf(),
    html.Br(),
    dbc.Card(
        dbc.CardBody(
            [dcc.Graph(id='fig_corr', figure=get_fig_corr(), style={'height': 1000}, responsive=True)],
        ), color='dark'
    ),
],
    style={
        'width': '90%',
        'margin-left': 35,
        'margin-top': 35,
        'margin-bottom': 35
    },
)


# # update instrument chart
# @callback(
#     Output('table_perf', 'children'),
#     Input('table_perf', 'virtualRowData'),
#     prevent_initial_call=True
# )
# def update_corr_heatmap_fig(virtual_data):
#     test = str(virtual_data)
#     return test
