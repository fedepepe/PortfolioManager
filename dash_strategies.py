import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import html, dcc

from dash_portfolio_data import get_portfolio_data
from definitions import Accounts
from portfolio_history import compute_hist_benchmark_data


# TODO: add benchmark(s)

# DASHBOARD
def build_content_strategies(account: Accounts):
	build_content_strategies.pf_data = get_portfolio_data(account=account)
	index = build_content_strategies.pf_data.hist_data.nav_eff.index
	build_content_strategies.bm_data = compute_hist_benchmark_data(account=account, index=index)
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
	                                      name='Portfolio',
	                                      mode='lines',
	                                      hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>')],
	                     layout=go.Layout(xaxis_title=dict(text='Date'),
	                                      yaxis_title=dict(text='NAV (adjusted)'),
	                                      template='plotly_dark')
	                     )
	fig_navs.add_trace(go.Scatter(x=build_content_strategies.bm_data.nav_eff.index,
	                              y=build_content_strategies.bm_data.nav_eff.values,
	                              name='Benchmark',
	                              mode='lines',
	                              hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
	return fig_navs


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
