import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import html, dcc, Input, Output, callback

from dashboard.dash_common import loading_wrapper
import dashboard.dash_sidebar as dash_sidebar
from dashboard.dash_portfolio_data import PortfolioData
from definitions import Accounts
from instruments_performance import prices_to_base_curr
from portfolio_history import compute_hist_benchmark_data
from reporting import OutDataTabs
from sql import query_yahoo_finance_prod_info, query_yahoo_finance_hist_data
from yfinance_api import YFinHistCols, YFinInfoCols


# DASHBOARD
def build_content_portfolio(account: Accounts):
	if not hasattr(build_content_portfolio, 'pf_data'):
		build_content_portfolio.pf_data = PortfolioData(account=account)
	index = build_content_portfolio.pf_data.hist_data.nav_eff.index
	if not hasattr(build_content_portfolio, 'bm_data'):
		build_content_portfolio.bm_data = compute_hist_benchmark_data(account=account, index=index)
	return html.Div(
		dbc.Card(
			dbc.CardBody([
				dbc.Row([
					dbc.Col([html.H2(draw_text(account.name))], style={"width": "10%"}),
					dbc.Col([dbc.Button("Update", id="button-update", className="me-2", n_clicks=0)],
					        style={"width": "10%"}),
					dbc.Col([html.H2("Text")], style={"width": "10%"}),
				], align='center'),
				html.Br(),
				dbc.Row([
					dbc.Col([loading_wrapper(dcc.Graph(id='fig_navs', figure=get_fig_navs()))], style={"width": "15%"}),
					dbc.Col([loading_wrapper(dcc.Graph(id='fig_comp', figure=get_fig_comp()))], style={"width": "10%"}),
					dbc.Col([loading_wrapper(dcc.Graph(id='fig_perf', figure=get_fig_perf()))], style={"width": "10%"}),
					dbc.Col([loading_wrapper(dcc.Graph(id='fig_corr', figure=get_fig_corr()))], style={"width": "10%"}),
				], align='center'),
				html.Br(),
				dbc.Row([
					dbc.Col([loading_wrapper(dcc.Graph(id='fig_pf_instr_adj_close', figure=get_fig_pf_instr_adj_close()))],
					        style={"width": "15%"}),
					dbc.Col(loading_wrapper([dcc.Graph(id='fig_risk_contrib', figure=get_fig_risk_contrib())]),
					        style={"width": "10%"}),
					dbc.Col(loading_wrapper([dcc.Graph(id='fig_monthly_ret', figure=get_fig_monthly_ret())]),
					        style={"width": "10%"}),
				], align='center'),
				html.Br(),
				dbc.Row([
					dbc.Col([
						dbc.Row([dbc.Input(id='input_isin', placeholder="Enter ISIN or ticker...", size="sm"),
						         loading_wrapper(dcc.Graph(id='fig_instr_adj_close', figure=get_fig_instr_adj_close())),
						         ], align='center')
					], style={"width": "15%"}),
				], align='center'),
				html.Br(),
			]), color='dark'
		),
	)


# NAV ADJUSTED LINE PLOT
def get_fig_navs() -> go.Figure:
	fig_navs = go.Figure(data=[go.Scatter(x=build_content_portfolio.pf_data.hist_data.nav_eff.index,
	                                      y=build_content_portfolio.pf_data.hist_data.nav_eff["NAV Effective"],
	                                      name='Portfolio',
	                                      mode='lines',
	                                      hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>')],
	                     layout=go.Layout(xaxis_title=dict(text='Date'),
	                                      yaxis_title=dict(text='NAV (adjusted)'),
	                                      template='plotly_dark')
	                     )
	fig_navs.add_trace(go.Scatter(x=build_content_portfolio.bm_data.nav_eff.index,
	                              y=build_content_portfolio.bm_data.nav_eff.values,
	                              name='Benchmark',
	                              mode='lines',
	                              hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	return fig_navs


# PORTFOLIO ALLOCATION PIE CHART
def get_fig_comp() -> go.Figure:
	return go.Figure(data=[go.Pie(labels=build_content_portfolio.pf_data.alloc_risk_df.index,
	                              values=build_content_portfolio.pf_data.alloc_risk_df['Allocation'],
	                              customdata=build_content_portfolio.pf_data.alloc_risk_df[['name']],
	                              hovertemplate="%{customdata[0]}<br>%{value:.2%}<br><extra></extra>",
	                              direction='clockwise',
	                              hole=.4,
	                              sort=False)],
	                 layout=go.Layout(
		                 title=dict(text="Portfolio allocation"),
		                 legend=dict(orientation='h', y=-0.1),
		                 template="plotly_dark"
	                 ))


# ASSET CORRELATION MATRIX HEATMAP
def get_fig_corr() -> go.Figure:
	corr_mat = build_content_portfolio.pf_data.returns_adj_weekly_df.corr()
	corr_mat = np.tril(corr_mat)
	corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
	corr_mat = pd.DataFrame(corr_mat, columns=build_content_portfolio.pf_data.hist_data.close_adj.columns,
	                        index=build_content_portfolio.pf_data.hist_data.close_adj.columns)
	corr_mat = corr_mat.loc[list(reversed(build_content_portfolio.pf_data.hist_data.close_adj.columns)), :].values
	return go.Figure(data=[go.Heatmap(z=corr_mat,
	                                  x=build_content_portfolio.pf_data.hist_data.close_adj.columns,
	                                  y=list(reversed(build_content_portfolio.pf_data.hist_data.close_adj.columns)),
	                                  colorscale='RdBu_r',
	                                  zmin=-1,
	                                  zmax=1,
	                                  xgap=1,
	                                  ygap=1,
	                                  hoverongaps=False)],
	                 layout=go.Layout(title=dict(text="Asset correlation matrix"),
	                                  template="plotly_dark",
	                                  xaxis=dict(side='top', scaleanchor="y", constrain="domain"),
	                                  yaxis=dict(scaleanchor="x", constrain="domain")
	                                  ))


# RISK ALLOCATION PIE CHART
def get_fig_risk_contrib() -> go.Figure:
	return go.Figure(data=[go.Pie(labels=build_content_portfolio.pf_data.alloc_risk_df.index,
	                              values=build_content_portfolio.pf_data.alloc_risk_df['Risk contrib.'],
	                              customdata=build_content_portfolio.pf_data.alloc_risk_df[['name']],
	                              hovertemplate="%{customdata[0]}<br>%{value:.2%}<br><extra></extra>",
	                              direction='clockwise',
	                              hole=.4,
	                              sort=False)],
	                 layout=go.Layout(
		                 title=dict(text="Risk allocation"),
		                 legend=dict(orientation='h', y=-0.1),
		                 template="plotly_dark"
	                 ))


# PERFORMANCE METRICS TABLE
def get_fig_perf() -> go.Figure:
	row_even_color = px.colors.qualitative.Plotly[2]
	row_odd_color = px.colors.qualitative.Plotly[0]
	perf_df = build_content_portfolio.pf_data.perf_dct[OutDataTabs.RISK_METRICS]
	return go.Figure(data=[go.Table(
		columnwidth=[120, 50],
		header=dict(
			values=['<b>Performance Metric</b>', '<b>Portfolio</b>'],
			align=['left', 'center'],
			height=25
		),
		cells=dict(
			values=perf_df.dropna().round(3).reset_index().T.values.tolist(),
			# 2-D list of colors for alternating rows
			fill_color=[[row_odd_color, row_even_color] * 10],
			align=['left', 'center'],
			height=22
		))],
		layout=go.Layout(template="plotly_dark",
		                 margin={"l": 30, "r": 30, "t": 30, "b": 30}
		                 )
	)


# MONTHLY RETURNS HISTOGRAM CHART
def get_fig_monthly_ret() -> go.Figure:
	return go.Figure(
		data=[go.Histogram(x=build_content_portfolio.pf_data.perf_dct[OutDataTabs.RETURNS_MONTHLY]['return'].values,
		                   histnorm='probability',
		                   name='return'
		                   )],
		layout=go.Layout(title=dict(text="Monthly returns distribution"),
		                 xaxis_title=dict(text='Return'),
		                 yaxis_title=dict(text='Frequency'),
		                 bargap=0.1,
		                 template="plotly_dark")
	)


# PORTFOLIO INSTRUMENTS ADJUSTED CLOSE
def get_fig_pf_instr_adj_close() -> go.Figure:
	currency = dash_sidebar.account.currency
	fig_pf_instr_adj_close = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
	                                                    yaxis_title=dict(
		                                                    text=f'Adjusted Closing Price [{currency}]'),
	                                                    template='plotly_dark')
	                                   )
	for col in build_content_portfolio.pf_data.alloc_risk_df.index.drop('Cash'):
		fig_pf_instr_adj_close.add_trace(go.Scatter(x=build_content_portfolio.pf_data.hist_data.close_adj.index,
		                                            y=build_content_portfolio.pf_data.hist_data.close_adj[col].ffill(),
		                                            name=col,
		                                            mode='lines',
		                                            hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	return fig_pf_instr_adj_close


# INSTRUMENT ADJUSTED CLOSE
def get_fig_instr_adj_close() -> go.Figure:
	return go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
	                                  yaxis_title=dict(text='Adjusted Closing Price'),
	                                  template='plotly_dark'))


# Text field
def draw_text(text: str):
	return html.Div([
		dbc.Card(
			dbc.CardBody([
				html.Div([
					html.H2(text),
				], style={'textAlign': 'center'})
			])
		),
	])


# update portfolio data charts
@callback(
	Output('fig_navs', 'figure'),
	Output('fig_comp', 'figure'),
	Output('fig_perf', 'figure'),
	Output('fig_corr', 'figure'),
	Output('fig_pf_instr_adj_close', 'figure'),
	Output('fig_risk_contrib', 'figure'),
	Output('fig_monthly_ret', 'figure'),
	Input('button-update', 'n_clicks'),
	prevent_initial_call=True
)
def update(n_clicks) -> (go.Figure, go.Figure, go.Figure, go.Figure, go.Figure, go.Figure, go.Figure):
	build_content_portfolio.pf_data.update()
	return (get_fig_navs(),
	        get_fig_comp(),
	        get_fig_perf(),
	        get_fig_corr(),
	        get_fig_pf_instr_adj_close(),
	        get_fig_risk_contrib(),
	        get_fig_monthly_ret())


# update instrument chart
@callback(
	Output('fig_instr_adj_close', 'figure'),
	Input('input_isin', 'value'),
	prevent_initial_call=True
)
def update_instr_adj_close_fig(isin) -> go.Figure:
	results_df = query_yahoo_finance_prod_info(ticker=isin)
	if len(isin) >= 4:
		results_df = pd.concat([results_df, query_yahoo_finance_prod_info(isin=isin)], axis=1)
	if not results_df.empty:
		ticker = results_df.iloc[:, 0][YFinInfoCols.symbol.value]
		name = results_df.iloc[:, 0][YFinInfoCols.name_long.value]
		currency = results_df.iloc[:, 0][YFinInfoCols.currency.value]
		df = query_yahoo_finance_hist_data(columns=YFinHistCols.adj_close,
		                                   tickers=ticker)
		df_base = prices_to_base_curr(account=dash_sidebar.account,
		                              price_df=df,
		                              curr_info=[currency])
		fig = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
		                                 yaxis_title=dict(text=f'Adjusted Closing Price'),
		                                 template='plotly_dark',
		                                 title=name,
		                                 showlegend=True))
		fig.add_trace(go.Scatter(x=df.index,
		                         y=df.iloc[:, 0],
		                         name=f'{df.columns[0]} [{currency}]',
		                         mode='lines',
		                         hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
		if not df_base.empty:
			fig.add_trace(go.Scatter(x=df_base.index,
			                         y=df_base.iloc[:, 0],
			                         name=f'{df.columns[0]} [{dash_sidebar.account.currency}]',
			                         mode='lines',
			                         hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	else:
		fig = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
		                                 yaxis_title=dict(text=f'Adjusted Closing Price'),
		                                 template='plotly_dark',
		                                 showlegend=True))
	return fig
