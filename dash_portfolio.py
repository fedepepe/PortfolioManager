# Run this app with `python app.py` and
# visit http://127.0.0.1:8050/ in your web browser.
from dash import Dash, html, dcc
import plotly.express as px
import pandas as pd
from definitions import Accounts
from portfolio_history import load_hist_portfolio_data


external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']

dash = Dash(__name__, requests_pathname_prefix="/portfolio/", external_stylesheets=external_stylesheets)

account = Accounts.CHF
data = load_hist_portfolio_data(portfolio_name=account.name)


fig_nav = px.line(data.nav_eff.reset_index().rename(columns={'timestamp': 'Date', 'NAV Effective': 'NAV (adjusted)'}),
                  x="Date", y="NAV (adjusted)")
fig_comp = px.pie(data.effective_weights.T.iloc[:, -1].rename('Weights').to_frame().reset_index(),
                  values='Weights', names='index')
fig_corr = px.imshow(data.close_adj.resample('W').last().pct_change().corr(), color_continuous_scale='RdBu_r')


dash.layout = html.Div(children=[
	html.H1(children=account.name),
	html.Div(children=[
		html.Div(
			dcc.Graph(
				id='fig_nav',
				figure=fig_nav
			), className='four columns'),
		html.Div(
			dcc.Graph(
				id='fig_comp',
				figure=fig_comp
			), className='four columns'),
		html.Div(
			dcc.Graph(
				id='fig_corr',
				figure=fig_corr
			), className='four columns'),
	], className='row')
])

if __name__ == '__main__':
	dash.run(debug=True)
