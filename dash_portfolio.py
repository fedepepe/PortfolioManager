# Run this app with `python app.py` and
# visit http://127.0.0.1:8050/ in your web browser.
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, html, dcc

from definitions import Accounts
from portfolio_analysis_funcs import vol_risk_contr
from portfolio_history import load_hist_portfolio_data
from portfolio_performance import load_portfolio_performance
from reporting import OutDataTabs

external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']

dash = Dash(__name__,
            requests_pathname_prefix="/portfolio/",
            external_stylesheets=external_stylesheets,
            meta_tags=[{"name": "portfolio", "content": "width=device-width"}])

account = Accounts.CHF

# load data
data_pf = load_hist_portfolio_data(portfolio_name=account.name)
data_perf = load_portfolio_performance(account=account)

fig_nav = px.line(
	data_pf.nav_eff.reset_index().rename(columns={'timestamp': 'Date', 'NAV Effective': 'NAV (adjusted)'}),
	x="Date", y="NAV (adjusted)",
	template="simple_white")
fig_nav.update_yaxes(showgrid=True)

weights_last = data_pf.effective_weights.T.iloc[:, -1].rename('Allocation')
risk_contrib = vol_risk_contr(w=data_pf.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
                              cov_mat=data_pf.close_adj.resample('W').last().pct_change().cov().values)
risk_contrib = pd.DataFrame(np.append(risk_contrib, 0.),
                            columns=['Risk contrib.'],
                            index=data_pf.effective_weights.columns)
alloc_risk_df = pd.concat([weights_last, risk_contrib], axis=1).sort_values(by='Allocation', ascending=False)

fig_comp = go.Figure(data=[go.Pie(labels=alloc_risk_df.index,
                                  values=alloc_risk_df['Allocation'],
                                  direction='clockwise',
                                  hole=.4,
                                  sort=False)],
                     layout=go.Layout(
	                     title=dict(text="Portfolio allocation")
                     ))
fig_corr = px.imshow(data_pf.close_adj.resample('W').last().pct_change().corr(),
                     title="Asset correlation matrix",
                     color_continuous_scale='RdBu_r',
                     range_color=[-1, 1])
fig_risk_contrib = go.Figure(data=[go.Pie(labels=alloc_risk_df.index,
                                          values=alloc_risk_df['Risk contrib.'],
                                          direction='clockwise',
                                          hole=.4,
                                          sort=False)],
                             layout=go.Layout(
	                             title=dict(text="Risk allocation")
                             ))

headerColor = 'grey'
rowEvenColor = 'lightgrey'
rowOddColor = 'white'

fig_perf = go.Figure(data=[go.Table(
	columnwidth=[120, 50],
	header=dict(
		values=['<b>Risk Metric</b>', '<b>Value</b>'],
		line_color='darkslategray', fill_color=rowEvenColor,
		align=['left', 'center'],
		font=dict(color='darkslategray', size=14),
		height=30
	),
	cells=dict(
		values=data_perf[OutDataTabs.RISK_METRICS].dropna().round(3).reset_index().transpose().values.tolist(),
		line_color='darkslategray',
		# 2-D list of colors for alternating rows
		fill_color=[[rowOddColor, rowEvenColor, rowOddColor, rowEvenColor, rowOddColor] * 5],
		align=['left', 'center'],
		font=dict(color='darkslategray', size=14),
		height=30
	))
])

fig_monthly_ret = px.histogram(data_perf[OutDataTabs.RETURNS_MONTHLY],
                               x="return",
                               histnorm='probability density',
                               title="Monthly returns distribution",
                               template="simple_white")

fig_monthly_ret.update_yaxes(showgrid=True)

dash.layout = html.Div(children=[
	html.H1(children=account.name),
	html.Div(children=[
		html.Div(
			dcc.Graph(
				id='fig_nav',
				figure=fig_nav
			), className='three columns', style={"border": "1px black solid"}),
		html.Div(
			dcc.Graph(
				id='fig_comp',
				figure=fig_comp
			), className='three columns', style={"border": "1px black solid"}),
		html.Div(
			dcc.Graph(
				id='fig_corr',
				figure=fig_corr
			), className='three columns', style={"border": "1px black solid"}),
	], className='row'),
	html.Div(children=[
		html.Div(
			dcc.Graph(
				id='fig_perf',
				figure=fig_perf
			), className='three columns', style={"border": "1px black solid"}),
		html.Div(
			dcc.Graph(
				id='fig_risk_contrib',
				figure=fig_risk_contrib
			), className='three columns', style={"border": "1px black solid"}),
		html.Div(
			dcc.Graph(
				id='fig_monthly_ret',
				figure=fig_monthly_ret
			), className='three columns', style={"border": "1px black solid"}),
	], className='row')
])

if __name__ == '__main__':
	dash.run(debug=True)
