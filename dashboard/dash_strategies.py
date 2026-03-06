import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import html, dcc

from dashboard.dash_common import LAYOUT_TEMPLATE
from dashboard.dash_portfolio_data import PortfolioData
from config.accounts import Accounts
from portfolio.portfolio_history import compute_hist_portfolio_data_optimized


# DASHBOARD
def build_content_strategies(account: Accounts):
	if not hasattr(build_content_strategies, 'account'):
		build_content_strategies.account = account
	if not hasattr(build_content_strategies, 'pf_data'):
		build_content_strategies.pf_data = PortfolioData(account=account)
	index = build_content_strategies.pf_data.hist_data.nav_eff.index
	if not hasattr(build_content_strategies, 'opt_data'):
		build_content_strategies.opt_data = compute_hist_portfolio_data_optimized(account=account, index=index)
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
	                                      template=LAYOUT_TEMPLATE,
	                                      legend=dict(orientation="h",
	                                                  yanchor="bottom",
	                                                  y=1.0,
	                                                  xanchor="right",
	                                                  x=1))
	                     )
	fig_navs.add_trace(go.Scatter(x=build_content_strategies.opt_data.nav_eff.index,
	                              y=build_content_strategies.opt_data.nav_eff.values,
	                              name='Optimized',
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
