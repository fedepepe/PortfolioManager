import time
from datetime import datetime
from typing import Optional, Tuple

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import html, dcc, Input, Output, State, callback
from dash.exceptions import PreventUpdate

from config.accounts import Accounts
from dashboard.dash_common import LAYOUT_TEMPLATE, loading_wrapper, card_wrapper, get_fig_empty, get_fig_metrics_table
from dashboard.dash_portfolio_data import PortfolioData
from dashboard.data_service import get_portfolio_data, get_optimized_data, run_optimization, OptimizedData
from engines.reporting import PerfDataTabs


# DASHBOARD
def build_content_strategies(account: Accounts):
    # layout only: the figures are filled by the render callback, which also runs when the page is loaded
    return html.Div(
        dbc.Card([
            dbc.Row([
                dbc.Col([dcc.Dropdown([acc.name for acc in Accounts], account.name,
                                      id='dropdown-strategies', clearable=False)],
                        style={"width": "15%"}),
                dbc.Col([dbc.Button("Run optimization", id="button-optimize", className="me-2", n_clicks=0)],
                        style={"width": "10%"}),
                dbc.Col([loading_wrapper(html.Div(id='optimize-status'))], style={"width": "10%"}),
                dbc.Col([], style={"width": "10%"}),
            ], align='center'),
            html.Br(),
            dbc.Row([
                dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_strat_navs', figure=get_fig_empty()))),
                        width=8),
                dbc.Col(loading_wrapper(card_wrapper(dcc.Graph(id='fig_strat_perf', figure=get_fig_empty()))),
                        width=4),
            ], align='center'),
            html.Br(),
        ], body=True, color='dark'
        ),
    )


# NAV ADJUSTED LINE PLOT: portfolio vs optimized portfolio
def get_fig_strat_navs(account: Accounts, pf_data: PortfolioData, opt_data: Optional[OptimizedData]) -> go.Figure:
    fig_navs = go.Figure(data=[go.Scatter(x=pf_data.hist_data.nav_eff.index,
                                          y=pf_data.hist_data.nav_eff.values,
                                          name=pf_data.hist_data.name,
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
    if opt_data is None:
        fig_navs.update_layout(title=dict(text=f'No optimization computed yet for {account.name}: '
                                               f'press "Run optimization"'))
        return fig_navs
    fig_navs.add_trace(go.Scatter(x=opt_data.hist_data.nav_eff.index,
                                  y=opt_data.hist_data.nav_eff.values,
                                  name=opt_data.hist_data.name,
                                  mode='lines',
                                  hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>'))
    last_opt = opt_data.hist_data.nav_eff.index.max()
    title = (f'Optimization computed on {datetime.fromtimestamp(opt_data.computed_at):%Y-%m-%d %H:%M}, '
             f'data until {last_opt:%Y-%m-%d}')
    if pf_data.hist_data.nav_eff.index.max() > last_opt:
        title += '<br><sup>Portfolio data is more recent: run the optimization again to include it</sup>'
    fig_navs.update_layout(title=dict(text=title))
    return fig_navs


# PERFORMANCE METRICS TABLE: portfolio vs optimized portfolio
def get_fig_strat_perf(pf_data: PortfolioData, opt_data: Optional[OptimizedData]) -> go.Figure:
    opt_metrics = None
    if opt_data is not None and opt_data.perf_dct is not None:
        opt_metrics = opt_data.perf_dct.get(PerfDataTabs.RISK_METRICS)
    return get_fig_metrics_table({'Portfolio': pf_data.perf_dct[PerfDataTabs.RISK_METRICS],
                                  'Optimized': opt_metrics})


# remember the selected account in the browser (shared with the Portfolio page)
@callback(
    Output('store-account', 'data', allow_duplicate=True),
    Input('dropdown-strategies', 'value'),
    prevent_initial_call=True
)
def select_account_strategies(account_name: str) -> str:
    return account_name


# draw the strategies figures from saved data only (also when the page is loaded and after an optimization)
@callback(
    Output('fig_strat_navs', 'figure'),
    Output('fig_strat_perf', 'figure'),
    Input('dropdown-strategies', 'value'),
    Input('store-strategy-version', 'data'),
)
def render_strategies(account_name: str, strategy_version) -> Tuple:
    account = Accounts.get_account_by_name(name=account_name)
    pf_data = get_portfolio_data(account)
    opt_data = get_optimized_data(account)
    figs = (get_fig_strat_navs(account, pf_data, opt_data),
            get_fig_strat_perf(pf_data, opt_data))
    # keep zoom and legend state across redraws of the same account
    return tuple(fig.update_layout(uirevision=account_name) for fig in figs)


# run the optimization of the selected account; the new version triggers the redraw of the figures
@callback(
    Output('store-strategy-version', 'data'),
    Output('optimize-status', 'children'),
    Input('button-optimize', 'n_clicks'),
    State('dropdown-strategies', 'value'),
    prevent_initial_call=True
)
def run_optimization_callback(n_clicks, account_name: str) -> Tuple:
    # Dash also calls this when the page is built (prevent_initial_call does not apply, since
    # store-strategy-version is outside the page): optimize only on an actual click
    if not n_clicks:
        raise PreventUpdate
    prices_summary = run_optimization(Accounts.get_account_by_name(name=account_name))
    return time.time(), f'Optimized {time.strftime("%H:%M")} (prices {prices_summary})'
