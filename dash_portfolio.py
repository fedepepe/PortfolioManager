# Run this app with `python app.py` and
# visit http://127.0.0.1:8050/ in your web browser.
import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, html, dcc

from definitions import Accounts
from portfolio_analysis_funcs import vol_risk_contr
from portfolio_history import load_hist_portfolio_data
from portfolio_performance import load_portfolio_performance
from products import load_portfolio_products
from reporting import OutDataTabs

# external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']

dash = Dash(__name__,
            requests_pathname_prefix="/portfolio/",
            external_stylesheets=[dbc.themes.SLATE],
            meta_tags=[{"name": "portfolio", "content": "width=device-width"}])

account = Accounts.CHF

# LOAD DATA
data_pf = load_hist_portfolio_data(portfolio_name=account.name)
data_perf = load_portfolio_performance(account=account)
products_df = load_portfolio_products(account=account)

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
returns_adj_weekly_df = data_pf.close_adj.resample('W').last().pct_change()
risk_contrib = vol_risk_contr(w=data_pf.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
                              cov_mat=returns_adj_weekly_df.cov().values)
risk_contrib = pd.DataFrame(np.append(risk_contrib, 0.),
                            columns=['Risk contrib.'],
                            index=data_pf.effective_weights.columns)
alloc_risk_df = pd.concat([weights_last, risk_contrib, products_df.set_index('symbol')['name']], axis=1)
alloc_risk_df = alloc_risk_df.sort_values(by='Allocation', ascending=False)
alloc_risk_df['name'] = alloc_risk_df['name'].fillna(alloc_risk_df.index.to_series())
row_cash = alloc_risk_df.iloc[alloc_risk_df.index == 'Cash', :]
alloc_risk_df = alloc_risk_df.drop('Cash', axis=0)
alloc_risk_df = pd.concat([alloc_risk_df, row_cash])
fig_comp = go.Figure(data=[go.Pie(labels=alloc_risk_df.index,
                                  values=alloc_risk_df['Allocation'],
                                  customdata=alloc_risk_df[['name']],
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
corr_mat = returns_adj_weekly_df.corr()
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
                                      xaxis=dict(side='top', scaleanchor="y", constrain="domain"),
                                      yaxis=dict(scaleanchor="x", constrain="domain")
                                      ))

# RISK ALLOCATION PIE CHART
fig_risk_contrib = go.Figure(data=[go.Pie(labels=alloc_risk_df.index,
                                          values=alloc_risk_df['Risk contrib.'],
                                          customdata=alloc_risk_df[['name']],
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
    layout=go.Layout(template="plotly_dark",
                     # margin={"l": 1, "r": 1, "t": 1, "b": 1}
                     )
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

# INSTRUMENTS ADJUSTED CLOSE
fig_instr_adj_close = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
                                                 yaxis_title=dict(text=f'Adjusted Closing Price [{account.currency}]'),
                                                 template='plotly_dark')
                                )
for col in alloc_risk_df.index.drop('Cash'):
    fig_instr_adj_close.add_trace(go.Scatter(x=data_pf.close_adj.index,
                                             y=data_pf.close_adj[col].ffill(),
                                             name=col,
                                             mode='lines'))


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


# DASHBOARD
dash.layout = dbc.Container([
    dbc.Card(
        dbc.CardBody([
            dbc.Row([
                dbc.Col([html.H2(draw_text(account.name))], width=2),
                dbc.Col([html.H2("Text")], width=2),
                dbc.Col([html.H2("Text")], width=2),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col([dcc.Graph(id='fig_nav', figure=fig_nav)], width=3),
                dbc.Col([dcc.Graph(id='fig_comp', figure=fig_comp)], width=2),
                dbc.Col([dcc.Graph(id='fig_perf', figure=fig_perf)], width=2),
                dbc.Col([dcc.Graph(id='fig_corr', figure=fig_corr)], width=2),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col([dcc.Graph(id='fig_instr_adj_close', figure=fig_instr_adj_close)], width=3),
                dbc.Col([dcc.Graph(id='fig_risk_contrib', figure=fig_risk_contrib)], width=2),
                dbc.Col([dcc.Graph(id='fig_monthly_ret', figure=fig_monthly_ret)], width=2),
            ], align='center'),
            html.Br(),
        ]), color='dark'
    )
], fluid=True)

if __name__ == '__main__':
    dash.run(debug=True)
