from typing import Optional, List

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import html, dcc, Input, Output, callback

from definitions import Accounts
from instruments_performance import compute_product_performance, load_etf_catalog_data, InstrPerfTableCols
from reporting import Metrics
from yfinance_api import YFinHistCols, YF_PROD_INFO_LABEL

account = Accounts.CHF
MAX_INSTR_CORR = 50


# LOAD DATA
class InstrumentsData:
	def __init__(self):
		data = load_etf_catalog_data(account=account)
		self.adj_close_df = data[YFinHistCols.adj_close]
		self.volume_df = data[YFinHistCols.volume]
		self.prod_info_df = data[YF_PROD_INFO_LABEL]
		self.perf_df = compute_product_performance(adj_close_df=self.adj_close_df,
		                                           volume_df=self.volume_df,
		                                           prod_info_df=self.prod_info_df)

	def update(self):
		self.__init__()


instr_data = InstrumentsData()
COLS_INFO_TABLE = [InstrPerfTableCols.ticker,
                   InstrPerfTableCols.isin,
                   InstrPerfTableCols.volume]
COLS_PERF_TABLE = [Metrics.PA_RETURN,
                   Metrics.LAST_YEAR_RETURN,
                   Metrics.ANN_3Y_RETURN,
                   Metrics.ANN_5Y_RETURN,
                   Metrics.VOLATILITY,
                   Metrics.SHARPE_RATIO,
                   Metrics.SORTINO_RATIO,
                   Metrics.MAX_DD]
LABELS_PERF_TABLE_EXT = COLS_INFO_TABLE + [m.name for m in COLS_PERF_TABLE]
COLUMN_DEFS = [{"field": m.name,
                "sort": m.sort,
                "filter": "agNumberColumnFilter",
                "valueFormatter": {"function": m.to_ag_grid_format_func()}
                } for m in COLS_PERF_TABLE]
COLUMN_DEFS = [{"field": f} for f in COLS_INFO_TABLE] + COLUMN_DEFS


# PERFORMANCE METRICS TABLE
def get_table_perf() -> dag.AgGrid:
	return dag.AgGrid(
		id="table_perf",
		className='ag-theme-alpine-dark',
		columnDefs=COLUMN_DEFS,
		rowData=instr_data.perf_df.reset_index()[LABELS_PERF_TABLE_EXT].to_dict("records"),
		columnSize="responsiveSizeToFit",
		defaultColDef={"filter": "agTextColumnFilter"},
		dashGridOptions={"animateRows": False,
		                 'enableCellTextSelection': True},
	)


# INSTRUMENTS CORRELATION MATRIX HEATMAP
def get_fig_corr_instr(ticker_lst: Optional[List] = None) -> go.Figure:
	if ticker_lst is not None:
		adj_close_corr = instr_data.adj_close_df[ticker_lst].iloc[:, :MAX_INSTR_CORR].copy()
	else:
		adj_close_corr = instr_data.adj_close_df.iloc[:, :MAX_INSTR_CORR].copy()
	corr_mat = adj_close_corr.resample('W-WED').last().pct_change().corr()
	corr_mat = np.tril(corr_mat)
	corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
	corr_mat = pd.DataFrame(corr_mat,
	                        columns=adj_close_corr.columns,
	                        index=adj_close_corr.columns)
	corr_mat = corr_mat.loc[list(reversed(adj_close_corr.columns)), :].values
	return go.Figure(data=[go.Heatmap(z=corr_mat,
	                                  x=adj_close_corr.columns,
	                                  y=list(reversed(adj_close_corr.columns)),
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
	                                  margin={"l": 30, "r": 30, "t": 130, "b": 30}
	                                  )
	                 )


# DASHBOARD
def build_content_instruments() -> html.Div:
	return html.Div([
		dbc.Card(
			dbc.CardBody([
				get_table_perf(),
				html.Br(),
				dcc.Graph(id='fig_corr_instr', figure=get_fig_corr_instr(), style={'height': 1000}, responsive=True)
			]), color='dark'
		)],
	)


# update instrument correlation matrix
@callback(
	Output('fig_corr_instr', 'figure'),
	Input('table_perf', 'virtualRowData'),
)
def update_corr_heatmap_fig(virtual_data) -> go.Figure:
	df = pd.DataFrame(virtual_data)
	ticker_lst = df['Ticker'].to_list()
	return get_fig_corr_instr(ticker_lst=ticker_lst)
