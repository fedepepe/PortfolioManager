import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import html, dcc

from dashboard.dash_common import LAYOUT_TEMPLATE
from dashboard.dash_portfolio_data import PortfolioData
from config.accounts import Accounts
from portfolio.portfolio_backtest import backtest_portfolio_optimized


# DASHBOARD
def build_content_strategies(account: Accounts):
	if not hasattr(build_content_strategies, 'account'):
		build_content_strategies.account = account
	if not hasattr(build_content_strategies, 'pf_data'):
		build_content_strategies.pf_data = PortfolioData(account=account)
	index = build_content_strategies.pf_data.hist_data.nav_eff.index
	if not hasattr(build_content_strategies, 'opt_pf_tangency'):
		build_content_strategies.opt_pf_tangency = backtest_portfolio_optimized(account=account, index=index)
	return html.Div(
		dbc.Card(
			dbc.CardBody([
				dbc.Row([
					dbc.Col([html.H2(draw_text(account.name))], style={"width": "10%"}),
					dbc.Col([html.H2("Text")], style={"width": "10%"}),
				], align='center'),
				html.Br(),
				dbc.Row([
					dbc.Col([dcc.Graph(id='fig_nav', figure=get_fig_navs())], style={"width": "15%"}),
				], align='center'),
				html.Br(),
			]), color='dark'
		),
	)


# NAV ADJUSTED LINE PLOT
def get_fig_navs() -> go.Figure:
	fig_navs = go.Figure(data=[go.Scatter(x=build_content_strategies.pf_data.hist_data.nav_eff.index,
	                                      y=build_content_strategies.pf_data.hist_data.nav_eff["NAV Effective"],
	                                      name=build_content_strategies.pf_data.hist_data.name,
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
	fig_navs.add_trace(go.Scatter(x=build_content_strategies.opt_pf_tangency.nav_eff.index,
	                              y=build_content_strategies.opt_pf_tangency.nav_eff.values,
	                              name=build_content_strategies.opt_pf_tangency.name,
	                              mode='lines',
	                              hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	return fig_navs


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
