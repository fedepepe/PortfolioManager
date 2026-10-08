"""Instruments page: ETF catalog table and correlations."""

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from dash.exceptions import PreventUpdate

from portfolio_manager.analytics.instruments import InstrPerfTableCols
from portfolio_manager.analytics.metrics import Metrics
from portfolio_manager.config.accounts import Accounts
from portfolio_manager.dashboard.common import compute_corr_mat, loading_wrapper
from portfolio_manager.dashboard.data_service import get_instruments_data
from portfolio_manager.dashboard.instruments_data import InstrumentsData

MAX_INSTR_CORR = 50
COLS_INFO_TABLE = [InstrPerfTableCols.ticker, InstrPerfTableCols.isin, InstrPerfTableCols.volume]
COLS_PERF_TABLE = [
    Metrics.PA_RETURN,
    Metrics.LAST_YEAR_RETURN,
    Metrics.ANN_3Y_RETURN,
    Metrics.ANN_5Y_RETURN,
    Metrics.VOLATILITY,
    Metrics.SHARPE_RATIO,
    Metrics.SORTINO_RATIO,
    Metrics.MAX_DD,
]
LABELS_PERF_TABLE_EXT = COLS_INFO_TABLE + [m.name for m in COLS_PERF_TABLE]
COLUMN_DEFS = [
    {
        'field': m.name,
        'sort': m.sort,
        'filter': 'agNumberColumnFilter',
        'valueFormatter': {'function': m.to_ag_grid_format_func()},
    }
    for m in COLS_PERF_TABLE
]
COLUMN_DEFS = [{'field': f} for f in COLS_INFO_TABLE] + COLUMN_DEFS


# PERFORMANCE METRICS TABLE
def get_table_perf(instr_data: InstrumentsData) -> dag.AgGrid:
    """Table of the performance metrics of the ETF catalog (sortable and filterable)."""
    return dag.AgGrid(
        id='table_perf',
        className='ag-theme-alpine-dark',
        columnDefs=COLUMN_DEFS,
        rowData=instr_data.perf_df.reset_index()[LABELS_PERF_TABLE_EXT].to_dict('records'),
        columnSize='responsiveSizeToFit',
        defaultColDef={'filter': 'agTextColumnFilter'},
        dashGridOptions={'animateRows': False, 'enableCellTextSelection': True},
    )


# INSTRUMENTS CORRELATION MATRIX HEATMAP
def get_fig_corr_instr(instr_data: InstrumentsData, ticker_lst: list | None = None) -> go.Figure:
    """Correlation matrix of the weekly returns of the given instruments (the first ones of the catalog if none)."""
    if ticker_lst is not None:
        ticker_lst = [t for t in ticker_lst if t in instr_data.adj_close_df.columns]
        adj_close_corr = instr_data.adj_close_df[ticker_lst].iloc[:, :MAX_INSTR_CORR].copy()
    else:
        adj_close_corr = instr_data.adj_close_df.iloc[:, :MAX_INSTR_CORR].copy()
    title = 'Instruments correlation matrix' if not adj_close_corr.empty else 'No instruments match the filter'
    return go.Figure(
        data=[
            go.Heatmap(
                z=compute_corr_mat(adj_close_corr.resample('W-WED').last().pct_change()),
                x=adj_close_corr.columns,
                y=list(reversed(adj_close_corr.columns)),
                colorscale='RdBu_r',
                zmin=-1,
                zmax=1,
                xgap=1,
                ygap=1,
                hoverongaps=False,
            )
        ],
        layout=go.Layout(
            title=dict(text=title),
            template='plotly_dark',
            xaxis=dict(side='top', scaleanchor='y', constrain='domain'),
            yaxis=dict(scaleanchor='x', constrain='domain'),
            margin={'l': 30, 'r': 30, 't': 130, 'b': 30},
        ),
    )


# DASHBOARD
def build_content_instruments(account: Accounts) -> html.Div:
    """Layout of the Instruments page."""
    instr_data = get_instruments_data(account)
    if instr_data is None:
        return html.Div(dbc.Card(dbc.CardBody(html.H4(f'No ETF catalog available for {account.name}')), color='dark'))
    return html.Div(
        [
            dbc.Card(
                dbc.CardBody(
                    [
                        get_table_perf(instr_data),
                        html.Br(),
                        loading_wrapper(
                            dcc.Graph(
                                id='fig_corr_instr',
                                figure=get_fig_corr_instr(instr_data),
                                style={'height': 1000},
                                responsive=True,
                            )
                        ),
                    ]
                ),
                color='dark',
            )
        ],
    )


# update instrument correlation matrix with the instruments shown in the table (after sorting and filtering)
@callback(
    Output('fig_corr_instr', 'figure'),
    Input('table_perf', 'virtualRowData'),
    State('store-account', 'data'),
)
def update_corr_heatmap_fig(virtual_data, account_name) -> go.Figure:
    """Correlations of the instruments currently shown in the table."""
    # None until the table has rendered its rows: keep the heatmap built with the page
    if virtual_data is None:
        raise PreventUpdate
    account = Accounts.get_account_by_name(name=account_name) or Accounts.get_default_account()
    instr_data = get_instruments_data(account)
    if instr_data is None:
        raise PreventUpdate
    ticker_lst = [row[InstrPerfTableCols.ticker] for row in virtual_data]
    return get_fig_corr_instr(instr_data, ticker_lst=ticker_lst)
