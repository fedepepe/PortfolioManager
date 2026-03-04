from typing import Optional, List

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import html, dcc, Input, Output, callback, ctx

from dashboard.dash_common import loading_wrapper, card_wrapper, compute_corr_mat, LAYOUT_TEMPLATE
from dashboard.dash_portfolio_data import PortfolioData, AllocationRiskLabels
from database.sql import query_yahoo_finance_prod_info
from definitions import Accounts
from portfolio.instruments_performance import prices_to_base_curr, fetch_instr_hist_data
from portfolio.portfolio_history import compute_hist_portfolio_data_benchmark
from engines.reporting import OutDataTabs, Metrics
from yahoo_finance.yahoo_finance import YFinHistCols, YFinInfoCols


# DASHBOARD
def build_content_portfolio(account: Accounts):
	if not hasattr(build_content_portfolio, 'account'):
		build_content_portfolio.account = account
	if not hasattr(build_content_portfolio, 'pf_data'):
		build_content_portfolio.pf_data = PortfolioData(account=account)
	index = build_content_portfolio.pf_data.hist_data.nav_eff.index
	if not hasattr(build_content_portfolio, 'bm_data'):
		build_content_portfolio.bm_data = compute_hist_portfolio_data_benchmark(account=account, index=index)
	return html.Div(
		dbc.Card([
			dbc.Row([
				dbc.Col([dcc.Dropdown([acc.name for acc in Accounts], account.name,
				                      id='dropdown-portfolio', clearable=False)],
				        style={"width": "15%"}),
				dbc.Col([dbc.Button("Update", id="button-update", className="me-2", n_clicks=0)],
				        style={"width": "10%"}),
				dbc.Col([], style={"width": "10%"}),
				dbc.Col([], style={"width": "10%"}),
			], align='center'),
			html.Br(),
			dbc.Row([
				dbc.Col([loading_wrapper(card_wrapper(dcc.Graph(id='fig_navs', figure=get_fig_navs())))], style={"width": "15%"}),
				dbc.Col([loading_wrapper(card_wrapper(dcc.Graph(id='fig_comp', figure=get_fig_allocation())))], style={"width": "10%"}),
				dbc.Col([loading_wrapper(card_wrapper(dcc.Graph(id='fig_perf', figure=get_fig_perf())))], style={"width": "10%"}),
				dbc.Col([loading_wrapper(card_wrapper(dcc.Graph(id='fig_corr', figure=get_fig_corr())))], style={"width": "10%"}),
			], align='center'),
			html.Br(),
			dbc.Row([
				dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_pf_instr_adj_close', figure=get_fig_pf_instr_adj_close()))),
				        style={"width": "15%"}),
				dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_risk_contrib', figure=get_fig_risk_contrib()))),
				        style={"width": "10%"}),
				dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_monthly_ret', figure=get_fig_monthly_ret()))),
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
			html.Br()
		], body=True, color='dark'
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
	                                      template=LAYOUT_TEMPLATE,
	                                      legend=dict(orientation="h",
	                                                  yanchor="bottom",
	                                                  y=1.0,
	                                                  xanchor="right",
	                                                  x=1))
	                     )
	fig_navs.add_trace(go.Scatter(x=build_content_portfolio.bm_data.nav_eff.index,
	                              y=build_content_portfolio.bm_data.nav_eff.values,
	                              name='Benchmark',
	                              mode='lines',
	                              hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	return fig_navs


# PORTFOLIO ALLOCATION PIE CHART
def get_fig_allocation() -> go.Figure:
	return go.Figure(data=[go.Pie(labels=build_content_portfolio.pf_data.alloc_risk_df[AllocationRiskLabels.SYMBOL],
	                              values=build_content_portfolio.pf_data.alloc_risk_df[AllocationRiskLabels.ALLOCATION],
	                              customdata=build_content_portfolio.pf_data.alloc_risk_df[[AllocationRiskLabels.NAME]],
	                              hovertemplate="%{customdata[0]}<br>%{value:.2%}<br><extra></extra>",
	                              direction='clockwise',
	                              hole=.4,
	                              sort=False)],
	                 layout=go.Layout(
		                 title=dict(text="Portfolio allocation"),
		                 legend=dict(orientation='h', y=-0.1),
		                 template=LAYOUT_TEMPLATE
	                 ))


# ASSET CORRELATION MATRIX HEATMAP
def get_fig_corr() -> go.Figure:
	corr_df = compute_corr_mat(build_content_portfolio.pf_data.returns_adj_weekly_df)
	col_name_dct = dict(build_content_portfolio.pf_data.prod_df['symbol'])
	labels = corr_df.rename(columns=col_name_dct).columns.to_list()
	return go.Figure(data=[go.Heatmap(z=corr_df.values,
	                                  x=labels,
	                                  y=list(reversed(labels)),
	                                  colorscale='RdBu_r',
	                                  zmin=-1,
	                                  zmax=1,
	                                  xgap=1,
	                                  ygap=1,
	                                  hoverongaps=False)],
	                 layout=go.Layout(title=dict(text="Asset correlation matrix"),
	                                  template=LAYOUT_TEMPLATE,
	                                  xaxis=dict(side='top', scaleanchor="y", constrain="domain"),
	                                  yaxis=dict(scaleanchor="x", constrain="domain")
	                                  ))


# RISK ALLOCATION PIE CHART
def get_fig_risk_contrib() -> go.Figure:
	return go.Figure(data=[go.Pie(labels=build_content_portfolio.pf_data.alloc_risk_df[AllocationRiskLabels.SYMBOL],
	                              values=build_content_portfolio.pf_data.alloc_risk_df[AllocationRiskLabels.RISK_CONTRIB],
	                              customdata=build_content_portfolio.pf_data.alloc_risk_df[[AllocationRiskLabels.NAME]],
	                              hovertemplate="%{customdata[0]}<br>%{value:.2%}<br><extra></extra>",
	                              direction='clockwise',
	                              hole=.4,
	                              sort=False)],
	                 layout=go.Layout(
		                 title=dict(text="Risk allocation"),
		                 legend=dict(orientation='h', y=-0.1),
		                 template=LAYOUT_TEMPLATE
	                 ))


# PERFORMANCE METRICS TABLE
def get_fig_perf() -> go.Figure:
	row_even_color = px.colors.qualitative.Plotly[2]
	row_odd_color = px.colors.qualitative.Plotly[0]
	perf_df = build_content_portfolio.pf_data.perf_dct[OutDataTabs.RISK_METRICS]
	perf_bm_df = build_content_portfolio.pf_data.perf_bm_dct[OutDataTabs.RISK_METRICS]
	perf_df = pd.concat([perf_df, perf_bm_df], axis=1)
	perf_df = perf_df.drop([Metrics.BETA_OVERALL.name, Metrics.SKEWNESS.name], errors='ignore')
	return go.Figure(data=[go.Table(
		columnwidth=[120, 50],
		header=dict(
			values=['<b>Performance Metric</b>', '<b>Portfolio</b>', '<b>Benchmark</b>'],
			align=['left', 'center'],
			height=25
		),
		cells=dict(
			values=perf_df.round(3).replace(np.nan, "").reset_index().T.values.tolist(),
			# 2-D list of colors for alternating rows
			fill_color=[[row_odd_color, row_even_color] * 10],
			align=['left', 'center'],
			height=22
		))],
		layout=go.Layout(template=LAYOUT_TEMPLATE,
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
		                 template=LAYOUT_TEMPLATE)
	)


# PORTFOLIO INSTRUMENTS ADJUSTED CLOSE
def get_fig_pf_instr_adj_close(is_visible: Optional[List[bool]] = None) -> go.Figure:
	if is_visible is None:
		currency = build_content_portfolio.account.currency
		fig_pf_instr_adj_close = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
		                                                    yaxis_title=dict(
			                                                    text=f'Adjusted Closing Price [{currency}]'),
		                                                    template=LAYOUT_TEMPLATE))
		close_adj_df = build_content_portfolio.pf_data.hist_data.close_adj.dropna(how='all')
		col_name_dct = dict(build_content_portfolio.pf_data.prod_df['symbol'])
		for col in build_content_portfolio.pf_data.alloc_risk_df.index.drop('Cash'):
			fig_pf_instr_adj_close.add_trace(go.Scatter(x=close_adj_df.index,
			                                            y=close_adj_df[col].ffill(),
			                                            name=col_name_dct[col],
			                                            mode='lines',
			                                            hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
		get_fig_pf_instr_adj_close.fig_pf_instr_adj_close = fig_pf_instr_adj_close
	else:
		df = build_content_portfolio.pf_data.hist_data.close_adj.copy()
		df = df.reindex(columns=build_content_portfolio.pf_data.alloc_risk_df.index.drop('Cash'))
		df = df.iloc[:, [v is True for v in is_visible]]
		df = df.ffill().dropna(how='all')
		df = df.apply(lambda x: x.div(x.dropna().iloc[0]).mul(100))
		df = df.reindex(columns=build_content_portfolio.pf_data.alloc_risk_df.index.drop('Cash'))
		trace_name_lst = [t.name for t in get_fig_pf_instr_adj_close.fig_pf_instr_adj_close.data]
		for col, visible, name in zip(df.columns, is_visible, trace_name_lst):
			if visible is True:
				get_fig_pf_instr_adj_close.fig_pf_instr_adj_close.update_traces(x=df.index,
				                                                                y=df[col],
				                                                                selector=({'name': name}))
	return get_fig_pf_instr_adj_close.fig_pf_instr_adj_close


# INSTRUMENT ADJUSTED CLOSE
def get_fig_instr_adj_close() -> go.Figure:
	return go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
	                                  yaxis_title=dict(text='Adjusted Closing Price'),
	                                  template=LAYOUT_TEMPLATE))


# update portfolio data charts or switch portfolio
@callback(
	Output('fig_navs', 'figure'),
	Output('fig_comp', 'figure'),
	Output('fig_perf', 'figure'),
	Output('fig_corr', 'figure'),
	Output('fig_pf_instr_adj_close', 'figure'),
	Output('fig_risk_contrib', 'figure'),
	Output('fig_monthly_ret', 'figure'),
	Input('button-update', 'n_clicks'),
	Input('dropdown-portfolio', 'value'),
	Input('fig_pf_instr_adj_close', 'restyleData'),
	prevent_initial_call=True
)
def update_switch_portfolio(n_clicks, portfolio_name, fig_pf_instr_adj_close_data
                            ) -> (go.Figure, go.Figure, go.Figure, go.Figure, go.Figure, go.Figure, go.Figure):
	if ctx.triggered_id == 'dropdown-portfolio':
		build_content_portfolio.account = Accounts.get_account_by_name(name=portfolio_name)
		build_content_portfolio.pf_data = PortfolioData(account=build_content_portfolio.account)
		index = build_content_portfolio.pf_data.hist_data.nav_eff.index
		build_content_portfolio.bm_data = compute_hist_portfolio_data_benchmark(account=build_content_portfolio.account,
		                                                                        index=index)
	if ctx.triggered_id == 'button-update':
		build_content_portfolio.pf_data.update()
		index = build_content_portfolio.pf_data.hist_data.nav_eff.index
		build_content_portfolio.bm_data = compute_hist_portfolio_data_benchmark(account=build_content_portfolio.account,
		                                                                        index=index)
	if ctx.triggered_id == 'fig_pf_instr_adj_close':
		is_visible = fig_pf_instr_adj_close_data[0]['visible']
	else:
		is_visible = None
	return (get_fig_navs(),
	        get_fig_allocation(),
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
		df = fetch_instr_hist_data(isin_lst=isin,
		                           columns=YFinHistCols.adj_close,
		                           ticker_lst=ticker,
		                           name_lst=name,
		                           to_portfolio_instr_table=None)[YFinHistCols.adj_close]
		df_base = prices_to_base_curr(account=build_content_portfolio.account,
		                              price_df=df,
		                              curr_info=[currency])
		fig = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
		                                 yaxis_title=dict(text=f'Adjusted Closing Price'),
		                                 template=LAYOUT_TEMPLATE,
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
			                         name=f'{df.columns[0]} [{build_content_portfolio.account.currency}]',
			                         mode='lines',
			                         hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	else:
		fig = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
		                                 yaxis_title=dict(text=f'Adjusted Closing Price'),
		                                 template=LAYOUT_TEMPLATE,
		                                 showlegend=True))
	return fig
