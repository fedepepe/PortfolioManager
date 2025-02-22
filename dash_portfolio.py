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

# LOAD DATA
data_pf = load_hist_portfolio_data(portfolio_name=account.name)
data_perf = load_portfolio_performance(account=account)

# NAV ADJUSTED LINE PLOT
fig_nav = go.Figure(data=[go.Scatter(x=data_pf.nav_eff.index,
                                     y=data_pf.nav_eff["NAV Effective"],
                                     mode='lines')],
                    layout=go.Layout(xaxis_title=dict(text='Date'),
                                     yaxis_title=dict(text='NAV (adjusted)'),
                                     template='plotly_dark')
                    )

# PORTFOLIO ALLOCATION PIE CHART
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
                         title=dict(text="Portfolio allocation"),
                         template="plotly_dark"
                     ))

# ASSET CORRELATION MATRIX HEATMAP
corr_mat = data_pf.close_adj.resample('W').last().pct_change().corr()
corr_mat = np.tril(corr_mat)
corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
corr_mat = pd.DataFrame(corr_mat, columns=data_pf.close_adj.columns, index=data_pf.close_adj.columns)
corr_mat = corr_mat.loc[list(reversed(data_pf.close_adj.columns)), :].values
fig_corr = go.Figure(data=[go.Heatmap(z=corr_mat,
                                      x=data_pf.close_adj.columns,
                                      y=list(reversed(data_pf.close_adj.columns)),
                                      colorscale='RdBu_r',
                                      zmin=-1,
                                      zmax=1,
                                      xgap=1,
                                      ygap=1,
                                      hoverongaps=False)],
                     layout=go.Layout(title=dict(text="Asset correlation matrix"),
                                      template="plotly_dark",
                                      xaxis=dict(side='top')
                                      ))

# RISK ALLOCATION PIE CHART
fig_risk_contrib = go.Figure(data=[go.Pie(labels=alloc_risk_df.index,
                                          values=alloc_risk_df['Risk contrib.'],
                                          direction='clockwise',
                                          hole=.4,
                                          sort=False)],
                             layout=go.Layout(
                                 title=dict(text="Risk allocation"),
                                 template="plotly_dark"
                             ))

# PERFORMANCE METRICS TABLE
rowEvenColor = px.colors.qualitative.Plotly[2]
rowOddColor = px.colors.qualitative.Plotly[0]
fig_perf = go.Figure(data=[go.Table(
    columnwidth=[120, 50],
    header=dict(
        values=['<b>Performance Metric</b>', '<b>Value</b>'],
        align=['left', 'center'],
        height=30
    ),
    cells=dict(
        values=data_perf[OutDataTabs.RISK_METRICS].dropna().round(3).reset_index().transpose().values.tolist(),
        # 2-D list of colors for alternating rows
        fill_color=[[rowOddColor, rowEvenColor] * 10],
        align=['left', 'center'],
        height=30
    ))],
    layout=go.Layout(template="plotly_dark")
)

# MONTHLY RETURNS HISTOGRAM CHART
fig_monthly_ret = go.Figure(data=[go.Histogram(x=data_perf[OutDataTabs.RETURNS_MONTHLY]['return'].values,
                                               histnorm='probability',
                                               name='return'
                                               )],
                            layout=go.Layout(title=dict(text="Monthly returns distribution"),
                                             xaxis_title=dict(text='Return'),
                                             yaxis_title=dict(text='Frequency [%]'),
                                             bargap=0.1,
                                             template="plotly_dark")
                            )

# DASHBOARD
dash.layout = html.Div(style={'backgroundColor': 'black'},
                       children=[
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
