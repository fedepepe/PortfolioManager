import time
from typing import Optional, List, Tuple

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import html, dcc, Input, Output, State, callback, ctx
from dash.exceptions import PreventUpdate

from config.accounts import Accounts
from dashboard.dash_common import COLOR_ROW_ODD, COLOR_ROW_EVEN
from dashboard.dash_common import loading_wrapper, card_wrapper, compute_corr_mat, LAYOUT_TEMPLATE
from dashboard.dash_portfolio_data import PortfolioData, AllocationRiskLabels
from dashboard.data_service import get_portfolio_data, get_benchmark_data, update_account
from database.sql import query_yahoo_finance_prod_info
from engines.reporting import PerfDataTabs, Metrics, to_str_risk_metrics
from portfolio.instruments_performance import prices_to_base_curr, fetch_instr_hist_data
from portfolio.portfolio_definitions import PortfolioBacktestData
from yahoo_finance.yahoo_finance import YFinHistCols, YFinInfoCols


# DASHBOARD
def build_content_portfolio(account: Accounts):
    # layout only: the figures are filled by the render callbacks, which also run when the page is loaded
    return html.Div(
        dbc.Card([
            dbc.Row([
                dbc.Col([dcc.Dropdown([acc.name for acc in Accounts], account.name,
                                      id='dropdown-portfolio', clearable=False)],
                        style={"width": "15%"}),
                dbc.Col([dbc.Button("Update", id="button-update", className="me-2", n_clicks=0)],
                        style={"width": "10%"}),
                dbc.Col([loading_wrapper(html.Div(id='update-status'))], style={"width": "10%"}),
                dbc.Col([], style={"width": "10%"}),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_navs', figure=get_fig_empty()))), style={'width': '10%'}),
                dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_comp', figure=get_fig_empty()))), style={'width': '10%'}),
                dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_perf', figure=get_fig_empty()))), style={'width': '10%'}),
                dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_corr', figure=get_fig_empty()))), style={'width': '10%'}),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col(loading_wrapper(
                    card_wrapper(dcc.Graph(id='fig_pf_hist_sharpe', figure=get_fig_empty()))), style={'width': '10%'}),
                dbc.Col(loading_wrapper(
                    card_wrapper(dcc.Graph(id='fig_risk_contrib', figure=get_fig_empty()))), style={'width': '10%'}),
                dbc.Col(loading_wrapper(
                    card_wrapper(dcc.Graph(id='fig_pf_instr_adj_close', figure=get_fig_empty()))), style={'width': '10%'}),
                dbc.Col(loading_wrapper(
                    card_wrapper(dcc.Graph(id='fig_monthly_ret', figure=get_fig_empty()))), style={'width': '10%'}),
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


# EMPTY PLACEHOLDER
def get_fig_empty() -> go.Figure:
    return go.Figure(layout=go.Layout(template=LAYOUT_TEMPLATE))


# NAV ADJUSTED LINE PLOT
def get_fig_navs(pf_data: PortfolioData, bm_data: PortfolioBacktestData) -> go.Figure:
    fig_navs = go.Figure(data=[go.Scatter(x=pf_data.hist_data.nav_eff.index,
                                          y=pf_data.hist_data.nav_eff.values,
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
    fig_navs.add_trace(go.Scatter(x=bm_data.nav_eff.index,
                                  y=bm_data.nav_eff.values,
                                  name='Benchmark',
                                  mode='lines',
                                  hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
    return fig_navs


# PORTFOLIO ALLOCATION PIE CHART
def get_fig_allocation(pf_data: PortfolioData) -> go.Figure:
    return go.Figure(data=[go.Pie(labels=pf_data.alloc_risk_df[AllocationRiskLabels.SYMBOL],
                                  values=pf_data.alloc_risk_df[AllocationRiskLabels.ALLOCATION],
                                  customdata=pf_data.alloc_risk_df[[AllocationRiskLabels.NAME]],
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
def get_fig_corr(pf_data: PortfolioData) -> go.Figure:
    corr_df = compute_corr_mat(pf_data.returns_adj_weekly_df)
    col_name_dct = dict(pf_data.prod_df['symbol'])
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
def get_fig_risk_contrib(pf_data: PortfolioData) -> go.Figure:
    return go.Figure(data=[go.Pie(labels=pf_data.alloc_risk_df[AllocationRiskLabels.SYMBOL],
                                  values=pf_data.alloc_risk_df[AllocationRiskLabels.RISK_CONTRIB],
                                  customdata=pf_data.alloc_risk_df[[AllocationRiskLabels.NAME]],
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
def get_fig_perf(pf_data: PortfolioData) -> go.Figure:
    perf_df = to_str_risk_metrics(pf_data.perf_dct[PerfDataTabs.RISK_METRICS])
    perf_bm_df = to_str_risk_metrics(pf_data.perf_bm_dct[PerfDataTabs.RISK_METRICS])
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
            fill_color=[[COLOR_ROW_ODD, COLOR_ROW_EVEN] * len(perf_df)],
            align=['left', 'center'],
            height=22
        ))],
        layout=go.Layout(template=LAYOUT_TEMPLATE,
                         margin={"l": 30, "r": 30, "t": 30, "b": 30}
                         )
    )


# HISTORICAL SHARPE RATIO LINE PLOT
def get_fig_hist_sharpe(pf_data: PortfolioData) -> go.Figure:
    hist_perf_df = pf_data.perf_dct[PerfDataTabs.HIST_PERF_METRICS]
    fig_sharpe = go.Figure(data=[go.Scatter(x=hist_perf_df.index,
                                            y=hist_perf_df[Metrics.SHARPE_RATIO.name],
                                            name='Portfolio',
                                            mode='lines',
                                            hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>')],
                           layout=go.Layout(xaxis_title=dict(text='Date'),
                                            yaxis_title=dict(text=Metrics.SHARPE_RATIO.name),
                                            template=LAYOUT_TEMPLATE,
                                            legend=dict(orientation="h",
                                                        yanchor="bottom",
                                                        y=1.0,
                                                        xanchor="right",
                                                        x=1))
                           )
    hist_perf_bm_df = pf_data.perf_bm_dct[PerfDataTabs.HIST_PERF_METRICS]
    fig_sharpe.add_trace(go.Scatter(x=hist_perf_bm_df.index,
                                    y=hist_perf_bm_df[Metrics.SHARPE_RATIO.name].values,
                                    name='Benchmark',
                                    mode='lines',
                                    hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
    return fig_sharpe


# MONTHLY RETURNS HISTOGRAM CHART
def get_fig_monthly_ret(pf_data: PortfolioData) -> go.Figure:
    return go.Figure(
        data=[go.Histogram(x=pf_data.perf_dct[PerfDataTabs.RETURNS_MONTHLY]['return'].values,
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
def get_fig_pf_instr_adj_close(pf_data: PortfolioData,
                               currency: str,
                               is_visible: Optional[List[bool]] = None) -> go.Figure:
    # with some instruments hidden, the visible ones are rebased to 100 to make them comparable
    instruments = pf_data.alloc_risk_df.index.drop('Cash')
    if is_visible is None:
        is_visible = [True] * len(instruments)
    close_adj_df = pf_data.hist_data.close_adj.dropna(how='all')
    rebase = not all(is_visible)
    if rebase:
        visible_df = pf_data.hist_data.close_adj[[c for c, v in zip(instruments, is_visible) if v]]
        visible_df = visible_df.ffill().dropna(how='all')
        visible_df = visible_df.apply(lambda x: x.div(x.dropna().iloc[0]).mul(100))
    y_label = 'Adjusted Closing Price (rebased to 100)' if rebase else f'Adjusted Closing Price [{currency}]'
    fig_pf_instr_adj_close = go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
                                                        yaxis_title=dict(text=y_label),
                                                        template=LAYOUT_TEMPLATE))
    col_name_dct = dict(pf_data.prod_df['symbol'])
    for col, visible in zip(instruments, is_visible):
        ser = visible_df[col] if rebase and visible else close_adj_df[col].ffill()
        fig_pf_instr_adj_close.add_trace(go.Scatter(x=ser.index,
                                                    y=ser,
                                                    name=col_name_dct[col],
                                                    visible=True if visible else 'legendonly',
                                                    mode='lines',
                                                    hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
    return fig_pf_instr_adj_close


def get_visibility_after_restyle(figure: Optional[dict], restyle_data: Optional[list]) -> Optional[List[bool]]:
    # visibility of each trace after a legend click, starting from the figure currently shown:
    # restyle_data is [{'visible': [values]}, [trace indices]], listing only the traces that changed
    if not figure or not figure.get('data'):
        return None
    is_visible = [trace.get('visible', True) is True for trace in figure['data']]
    if not restyle_data or 'visible' not in restyle_data[0]:
        return is_visible
    values = restyle_data[0]['visible']
    values = values if isinstance(values, list) else [values]
    indices = restyle_data[1] if len(restyle_data) > 1 and restyle_data[1] is not None else range(len(is_visible))
    for n, idx in enumerate(indices):
        if idx < len(is_visible):
            is_visible[idx] = values[n % len(values)] is True
    return is_visible


# INSTRUMENT ADJUSTED CLOSE
def get_fig_instr_adj_close(title: Optional[str] = None) -> go.Figure:
    return go.Figure(layout=go.Layout(xaxis_title=dict(text='Date'),
                                      yaxis_title=dict(text='Adjusted Closing Price'),
                                      template=LAYOUT_TEMPLATE,
                                      title=title))


def with_ui_revision(fig: go.Figure, account_name: str) -> go.Figure:
    # keep zoom and legend state across redraws of the same account
    return fig.update_layout(uirevision=account_name)


# remember the selected account in the browser
@callback(
    Output('store-account', 'data'),
    Input('dropdown-portfolio', 'value'),
)
def select_account(account_name: str) -> str:
    return account_name


# draw the portfolio figures (also when the page is loaded and after an update)
@callback(
    Output('fig_navs', 'figure'),
    Output('fig_comp', 'figure'),
    Output('fig_perf', 'figure'),
    Output('fig_corr', 'figure'),
    Output('fig_pf_hist_sharpe', 'figure'),
    Output('fig_risk_contrib', 'figure'),
    Output('fig_monthly_ret', 'figure'),
    Input('dropdown-portfolio', 'value'),
    Input('store-data-version', 'data'),
)
def render_portfolio(account_name: str, data_version) -> Tuple:
    account = Accounts.get_account_by_name(name=account_name)
    pf_data = get_portfolio_data(account)
    bm_data = get_benchmark_data(account)
    figs = (get_fig_navs(pf_data, bm_data),
            get_fig_allocation(pf_data),
            get_fig_perf(pf_data),
            get_fig_corr(pf_data),
            get_fig_hist_sharpe(pf_data),
            get_fig_risk_contrib(pf_data),
            get_fig_monthly_ret(pf_data))
    return tuple(with_ui_revision(fig, account_name) for fig in figs)


# draw the portfolio instruments chart; a legend click rebases the visible instruments
@callback(
    Output('fig_pf_instr_adj_close', 'figure'),
    Input('dropdown-portfolio', 'value'),
    Input('store-data-version', 'data'),
    Input('fig_pf_instr_adj_close', 'restyleData'),
    State('fig_pf_instr_adj_close', 'figure'),
)
def render_pf_instr_adj_close(account_name: str, data_version, restyle_data, figure) -> go.Figure:
    account = Accounts.get_account_by_name(name=account_name)
    pf_data = get_portfolio_data(account)
    is_visible = None
    if ctx.triggered_id == 'fig_pf_instr_adj_close':
        is_visible = get_visibility_after_restyle(figure, restyle_data)
        if is_visible is not None and len(is_visible) != len(pf_data.alloc_risk_df.index.drop('Cash')):
            is_visible = None
    fig = get_fig_pf_instr_adj_close(pf_data, currency=account.currency, is_visible=is_visible)
    return with_ui_revision(fig, account_name)


# update the data of the selected account; the new data version triggers the redraw of the figures
@callback(
    Output('store-data-version', 'data'),
    Output('update-status', 'children'),
    Input('button-update', 'n_clicks'),
    State('dropdown-portfolio', 'value'),
    prevent_initial_call=True
)
def run_update(n_clicks, account_name: str) -> Tuple:
    # Dash also calls this when the page is built (prevent_initial_call does not apply, since
    # store-data-version is outside the page): update only on an actual click
    if not n_clicks:
        raise PreventUpdate
    update_account(Accounts.get_account_by_name(name=account_name))
    return time.time(), f'Updated {time.strftime("%H:%M")}'


# update instrument chart
@callback(
    Output('fig_instr_adj_close', 'figure'),
    Input('input_isin', 'value'),
    State('dropdown-portfolio', 'value'),
    prevent_initial_call=True
)
def update_instr_adj_close_fig(isin, account_name: str) -> go.Figure:
    # cleared or blank input: show the empty chart (an empty pattern would match every ticker)
    isin = (isin or '').strip()
    if not isin:
        return get_fig_instr_adj_close()
    account = Accounts.get_account_by_name(name=account_name)
    results_df = query_yahoo_finance_prod_info(ticker=isin)
    if len(isin) >= 4:
        results_df = pd.concat([results_df, query_yahoo_finance_prod_info(isin=isin)], axis=1)
    if results_df.empty:
        return get_fig_instr_adj_close(title=f'No instrument found for "{isin}"')
    ticker = results_df.iloc[:, 0][YFinInfoCols.symbol.value]
    name = results_df.iloc[:, 0][YFinInfoCols.name_long.value]
    currency = results_df.iloc[:, 0][YFinInfoCols.currency.value]
    df = fetch_instr_hist_data(isin_lst=isin,
                               columns=YFinHistCols.adj_close,
                               ticker_lst=ticker,
                               name_lst=name)[YFinHistCols.adj_close]
    # Yahoo Finance may return no usable history for the matched instrument
    df = df.dropna(axis=1, how='all')
    if df.empty:
        return get_fig_instr_adj_close(title=f'No price history found for {name} ({ticker})')
    df_base = prices_to_base_curr(account=account,
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
                                 name=f'{df.columns[0]} [{account.currency}]',
                                 mode='lines',
                                 hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
    return fig
