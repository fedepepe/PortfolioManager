import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import html, dcc, Input, Output, callback

from definitions import Accounts
from portfolio_analysis_funcs import vol_risk_contr
from portfolio_history import load_hist_portfolio_data, update_data, compute_hist_portfolio_data
from portfolio_performance import load_portfolio_performance, compute_portfolio_performance
from products import load_portfolio_products
from reporting import OutDataTabs
from sql import query_yahoo_finance_prod_info, query_yahoo_finance_hist_data
from yfinance_api import YFinHistCols, YFinInfoCols


# LOAD DATA
class PortfolioData:
	def __init__(self, account: Accounts):
		self.account = account
		self.hist_data = load_hist_portfolio_data(account=self.account)
		self.perf_dct = load_portfolio_performance(account=self.account)
		self.prod_df = load_portfolio_products(account=self.account)
		self.returns_adj_weekly_df = self.hist_data.close_adj.resample('W-WED').last().pct_change()
		self.alloc_risk_df = self.get_alloc_risk()

	def update(self):
		update_data(account=self.account)
		compute_hist_portfolio_data(account=self.account)
		compute_portfolio_performance(account=self.account)
		self.__init__(account=self.account)

	def get_alloc_risk(self) -> pd.DataFrame:
		weights_last = self.hist_data.effective_weights.T.iloc[:, -1].rename('Allocation')
		risk_contrib = vol_risk_contr(w=self.hist_data.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
		                              cov_mat=self.returns_adj_weekly_df.cov().values)
		risk_contrib = pd.DataFrame(np.append(risk_contrib, 0.),
		                            columns=['Risk contrib.'],
		                            index=self.hist_data.effective_weights.columns)
		alloc_risk_df = pd.concat([weights_last, risk_contrib, self.prod_df.set_index('symbol')['name']], axis=1)
		alloc_risk_df = alloc_risk_df.sort_values(by='Allocation', ascending=False)
		alloc_risk_df['name'] = alloc_risk_df['name'].fillna(alloc_risk_df.index.to_series())
		row_cash = alloc_risk_df.iloc[alloc_risk_df.index == 'Cash', :]
		alloc_risk_df = alloc_risk_df.drop('Cash', axis=0)
		return pd.concat([alloc_risk_df, row_cash])


# DASHBOARD
def build_content_portfolio(account: Accounts):
	build_content_portfolio.pf_data = PortfolioData(account=account)
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
					dbc.Col([dcc.Graph(id='fig_nav', figure=get_fig_nav())], style={"width": "15%"}),
					dbc.Col([dcc.Graph(id='fig_comp', figure=get_fig_comp())], style={"width": "10%"}),
					dbc.Col([dcc.Graph(id='fig_perf', figure=get_fig_perf())], style={"width": "10%"}),
					dbc.Col([dcc.Graph(id='fig_corr', figure=get_fig_corr())], style={"width": "10%"}),
				], align='center'),
				html.Br(),
				dbc.Row([
					dbc.Col([dcc.Graph(id='fig_pf_instr_adj_close', figure=get_fig_pf_instr_adj_close())],
					        style={"width": "15%"}),
					dbc.Col([dcc.Graph(id='fig_risk_contrib', figure=get_fig_risk_contrib())], style={"width": "10%"}),
					dbc.Col([dcc.Graph(id='fig_monthly_ret', figure=get_fig_monthly_ret())], style={"width": "10%"}),
				], align='center'),
				html.Br(),
				dbc.Row([
					dbc.Col([
						dbc.Row([dbc.Input(id='input_isin', placeholder="Enter ISIN or ticker...", size="sm"),
						         dcc.Graph(id='fig_instr_adj_close', figure=get_fig_instr_adj_close()),
						         ], align='center')
					], style={"width": "15%"}),
				], align='center'),
				html.Br(),
			]), color='dark'
		),
	)


# NAV ADJUSTED LINE PLOT
def get_fig_nav() -> go.Figure:
	return go.Figure(data=[go.Scatter(x=build_content_portfolio.pf_data.hist_data.nav_eff.index,
	                                  y=build_content_portfolio.pf_data.hist_data.nav_eff["NAV Effective"],
	                                  mode='lines',
	                                  hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>')],
	                 layout=go.Layout(xaxis_title=dict(text='Date'),
	                                  yaxis_title=dict(text='NAV (adjusted)'),
	                                  template='plotly_dark')
	                 )


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
	return go.Figure(data=[go.Table(
		columnwidth=[120, 50],
		header=dict(
			values=['<b>Performance Metric</b>', '<b>Value</b>'],
			align=['left', 'center'],
			height=30
		),
		cells=dict(
			values=build_content_portfolio.pf_data.perf_dct[OutDataTabs.RISK_METRICS].dropna().round(
				3).reset_index().T.values.tolist(),
			# 2-D list of colors for alternating rows
			fill_color=[[row_odd_color, row_even_color] * 10],
			align=['left', 'center'],
			height=30
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
		                 yaxis_title=dict(text='Frequency [%]'),
		                 bargap=0.1,
		                 template="plotly_dark")
		)


# PORTFOLIO INSTRUMENTS ADJUSTED CLOSE
def get_fig_pf_instr_adj_close() -> go.Figure:
	currency = build_content_portfolio.pf_data.account.currency
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
	Output('fig_nav', 'figure'),
	Output('fig_comp', 'figure'),
	Output('fig_perf', 'figure'),
	Output('fig_corr', 'figure'),
	Output('fig_pf_instr_adj_close', 'figure'),
	Output('fig_risk_contrib', 'figure'),
	Output('fig_monthly_ret', 'figure'),
	Input('button-update', 'n_clicks'),
	prevent_initial_call=True
)
def update(n_clicks) -> (go.Figure, go.Figure):
	build_content_portfolio.pf_data.update()
	return (get_fig_nav(),
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
		results_df = query_yahoo_finance_prod_info(isin=isin)
	if not results_df.empty:
		ticker = results_df.iloc[:, 0][YFinInfoCols.symbol.value]
		name = results_df.iloc[:, 0][YFinInfoCols.name_long.value]
		currency = results_df.iloc[:, 0][YFinInfoCols.currency.value]
		df = query_yahoo_finance_hist_data(column=YFinHistCols.adj_close,
		                                   ticker=ticker)
		fig = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
		                                 yaxis_title=dict(text=f'Adjusted Closing Price '
		                                                       f'[{currency}]'),
		                                 template='plotly_dark',
		                                 title=name,
		                                 showlegend=True))
		fig.add_trace(go.Scatter(x=df.index,
		                         y=df.iloc[:, 0],
		                         name=df.columns[0],
		                         mode='lines',
		                         hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	else:
		fig = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
		                                 yaxis_title=dict(text=f'Adjusted Closing Price'),
		                                 template='plotly_dark',
		                                 showlegend=True))
	return fig
