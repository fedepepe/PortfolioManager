import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import html, dcc

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
cols_info_table = [InstrPerfTableCols.ticker,
                   InstrPerfTableCols.isin,
                   InstrPerfTableCols.volume]
cols_perf_table = [Metrics.PA_RETURN,
                   Metrics.LAST_YEAR_RETURN,
                   Metrics.ANN_3Y_RETURN,
                   Metrics.ANN_5Y_RETURN,
                   Metrics.VOLATILITY,
                   Metrics.SHARPE_RATIO,
                   Metrics.SORTINO_RATIO,
                   Metrics.MAX_DD]
labels_perf_table_ext = cols_info_table + [m.name for m in cols_perf_table]
column_defs = [{"field": m.name,
                "sort": m.sort,
                "filter": "agNumberColumnFilter",
                "valueFormatter": {"function": m.to_ag_grid_format_func()}
                } for m in cols_perf_table]
column_defs = [{"field": f} for f in cols_info_table] + column_defs


# PERFORMANCE METRICS TABLE
def get_table_perf() -> dag.AgGrid:
	return dag.AgGrid(
		id="table_perf",
		className='ag-theme-alpine-dark',
		columnDefs=column_defs,
		rowData=instr_data.perf_df.reset_index()[labels_perf_table_ext].to_dict("records"),
		columnSize="responsiveSizeToFit",
		defaultColDef={"filter": "agTextColumnFilter"},
		dashGridOptions={"animateRows": False,
		                 'enableCellTextSelection': True},
	)


# INSTRUMENTS CORRELATION MATRIX HEATMAP
def get_fig_corr() -> go.Figure:
	# TODO: order by best performance metric and take top MAX_INSTR_CORR
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
	                                  margin={"l": 30, "r": 30, "t": 130, "b": 30}
	                                  )
	                 )


# DASHBOARD
content_instruments = html.Div([
	dbc.Card(
		dbc.CardBody([
			get_table_perf(),
			html.Br(),
			dcc.Graph(id='fig_corr', figure=get_fig_corr(), style={'height': 1000}, responsive=True)
		]), color='dark'
	)],
)

# # update instrument chart
# @callback(
#     Output('fig_corr', 'figure'),
#     Input('table_perf', 'virtualRowData'),
#     prevent_initial_call=True
# )
# def update_corr_heatmap_fig(virtual_data):
#     return get_fig_corr()
