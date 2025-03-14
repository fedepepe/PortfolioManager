import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, html, dcc, Input, Output, callback

from definitions import Accounts
from instruments_performance import fetch_instr_hist_data
from portfolio_analysis_funcs import vol_risk_contr
from portfolio_history import load_hist_portfolio_data, update_data, compute_hist_portfolio_data
from portfolio_performance import load_portfolio_performance, compute_portfolio_performance
from products import load_portfolio_products
from reporting import OutDataTabs
from yfinance_api import YFinHistCols
from products import get_product_info_from_isin

# external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']

dash = Dash(__name__,
            requests_pathname_prefix="/portfolio/",
            external_stylesheets=[dbc.themes.SLATE],
            meta_tags=[{"name": "portfolio", "content": "width=device-width"}])

account = Accounts.CHF


# LOAD DATA
class PortfolioData:
    def __init__(self):
        self.hist_df = load_hist_portfolio_data(account=account)
        self.perf_dct = load_portfolio_performance(account=account)
        self.prod_df = load_portfolio_products(account=account)
        self.returns_adj_weekly_df = self.hist_df.close_adj.resample('W-WED').last().pct_change()
        self.alloc_risk_df = self.get_alloc_risk()

    def update(self):
        update_data(account=account)
        compute_hist_portfolio_data(account=account)
        compute_portfolio_performance(account=account)
        self.__init__()

    def get_alloc_risk(self) -> pd.DataFrame:
        weights_last = self.hist_df.effective_weights.T.iloc[:, -1].rename('Allocation')
        risk_contrib = vol_risk_contr(w=self.hist_df.effective_weights.drop('Cash', axis=1).iloc[-1, :].values,
                                      cov_mat=self.returns_adj_weekly_df.cov().values)
        risk_contrib = pd.DataFrame(np.append(risk_contrib, 0.),
                                    columns=['Risk contrib.'],
                                    index=self.hist_df.effective_weights.columns)
        alloc_risk_df = pd.concat([weights_last, risk_contrib, self.prod_df.set_index('symbol')['name']], axis=1)
        alloc_risk_df = alloc_risk_df.sort_values(by='Allocation', ascending=False)
        alloc_risk_df['name'] = alloc_risk_df['name'].fillna(alloc_risk_df.index.to_series())
        row_cash = alloc_risk_df.iloc[alloc_risk_df.index == 'Cash', :]
        alloc_risk_df = alloc_risk_df.drop('Cash', axis=0)
        return pd.concat([alloc_risk_df, row_cash])


pf_data = PortfolioData()


# NAV ADJUSTED LINE PLOT
def get_fig_nav() -> go.Figure:
    return go.Figure(data=[go.Scatter(x=pf_data.hist_df.nav_eff.index,
                                      y=pf_data.hist_df.nav_eff["NAV Effective"],
                                      mode='lines')],
                     layout=go.Layout(xaxis_title=dict(text='Date'),
                                      yaxis_title=dict(text='NAV (adjusted)'),
                                      template='plotly_dark')
                     )


# PORTFOLIO ALLOCATION PIE CHART
def get_fig_comp() -> go.Figure:
    return go.Figure(data=[go.Pie(labels=pf_data.alloc_risk_df.index,
                                  values=pf_data.alloc_risk_df['Allocation'],
                                  customdata=pf_data.alloc_risk_df[['name']],
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
    corr_mat = pf_data.returns_adj_weekly_df.corr()
    corr_mat = np.tril(corr_mat)
    corr_mat[np.triu_indices(corr_mat.shape[0], 1)] = np.nan
    corr_mat = pd.DataFrame(corr_mat, columns=pf_data.hist_df.close_adj.columns,
                            index=pf_data.hist_df.close_adj.columns)
    corr_mat = corr_mat.loc[list(reversed(pf_data.hist_df.close_adj.columns)), :].values
    return go.Figure(data=[go.Heatmap(z=corr_mat,
                                      x=pf_data.hist_df.close_adj.columns,
                                      y=list(reversed(pf_data.hist_df.close_adj.columns)),
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
    return go.Figure(data=[go.Pie(labels=pf_data.alloc_risk_df.index,
                                  values=pf_data.alloc_risk_df['Risk contrib.'],
                                  customdata=pf_data.alloc_risk_df[['name']],
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
            values=pf_data.perf_dct[OutDataTabs.RISK_METRICS].dropna().round(3).reset_index().T.values.tolist(),
            # 2-D list of colors for alternating rows
            fill_color=[[row_odd_color, row_even_color] * 10],
            align=['left', 'center'],
            height=30
        ))],
        layout=go.Layout(template="plotly_dark",
                         # margin={"l": 1, "r": 1, "t": 1, "b": 1}
                         )
    )


# MONTHLY RETURNS HISTOGRAM CHART
def get_fig_monthly_ret() -> go.Figure:
    return go.Figure(data=[go.Histogram(x=pf_data.perf_dct[OutDataTabs.RETURNS_MONTHLY]['return'].values,
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
    fig_pf_instr_adj_close = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
                                                        yaxis_title=dict(
                                                            text=f'Adjusted Closing Price [{account.currency}]'),
                                                        template='plotly_dark')
                                       )
    for col in pf_data.alloc_risk_df.index.drop('Cash'):
        fig_pf_instr_adj_close.add_trace(go.Scatter(x=pf_data.hist_df.close_adj.index,
                                                    y=pf_data.hist_df.close_adj[col].ffill(),
                                                    name=col,
                                                    mode='lines'))
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


# DASHBOARD
dash.layout = dbc.Container([
    dbc.Card(
        dbc.CardBody([
            dbc.Row([
                dbc.Col([html.H2(draw_text(account.name))], width=2),
                dbc.Col([dbc.Button("Update", id="update-button", className="me-2", n_clicks=0)], width=2),
                dbc.Col([html.H2("Text")], width=2),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col([dcc.Graph(id='fig_nav', figure=get_fig_nav())], width=3),
                dbc.Col([dcc.Graph(id='fig_comp', figure=get_fig_comp())], width=2),
                dbc.Col([dcc.Graph(id='fig_perf', figure=get_fig_perf())], width=2),
                dbc.Col([dcc.Graph(id='fig_corr', figure=get_fig_corr())], width=2),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col([dcc.Graph(id='fig_pf_instr_adj_close', figure=get_fig_pf_instr_adj_close())], width=3),
                dbc.Col([dcc.Graph(id='fig_risk_contrib', figure=get_fig_risk_contrib())], width=2),
                dbc.Col([dcc.Graph(id='fig_monthly_ret', figure=get_fig_monthly_ret())], width=2),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col([
                    dbc.Row([dbc.Input(id='input_isin', placeholder="Enter ISIN or ticker...", size="sm"),
                             dcc.Graph(id='fig_instr_adj_close', figure=get_fig_instr_adj_close()),
                             ], align='center')
                ], width=3),
            ], align='center'),
            html.Br(),
        ]), color='dark'
    )
], fluid=True)


# update portfolio data charts
@callback(
    Output('fig_nav', 'figure'),
    Output('fig_comp', 'figure'),
    Input('update-button', 'n_clicks'),
    prevent_initial_call=True
)
def update(n_clicks):
    pf_data.update()
    return get_fig_nav(), get_fig_comp()


# update instrument chart
@callback(
    Output('fig_instr_adj_close', 'figure'),
    Input('input_isin', 'value'),
    prevent_initial_call=True
)
def update_instr_adj_close_fig(isin):
    results_df = get_product_info_from_isin(product_isin=isin)
    ticker = results_df['symbol'].mode().iloc[0]
    data = fetch_instr_hist_data(isin_lst=isin,
                                 columns=YFinHistCols.adj_close,
                                 tickers_rename=ticker)
    fig = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
                                     yaxis_title=dict(text=f'Adjusted Closing Price '
                                                           f'[{data[YFinHistCols.currency].iloc[0, 0]}]'),
                                     template='plotly_dark',
                                     showlegend=True))
    fig.add_trace(go.Scatter(x=data[YFinHistCols.adj_close].index,
                             y=data[YFinHistCols.adj_close].iloc[:, 0],
                             name=data[YFinHistCols.adj_close].columns[0],
                             mode='lines'))
    return fig


if __name__ == '__main__':
    dash.run(debug=True)
