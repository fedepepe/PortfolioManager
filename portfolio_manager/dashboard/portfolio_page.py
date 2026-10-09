"""Portfolio page: layout, figures and callbacks."""

import logging
import time

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from portfolio_manager.analytics.instruments import fetch_instr_hist_data, prices_to_base_curr
from portfolio_manager.analytics.metrics import Metrics, PerfDataTabs
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.config.accounts import Account, get_account, list_accounts
from portfolio_manager.dashboard.common import (
    LAYOUT_TEMPLATE,
    card_wrapper,
    compute_corr_mat,
    get_fig_empty,
    get_fig_metrics_table,
    loading_wrapper,
)
from portfolio_manager.dashboard.data_service import (
    get_benchmark_data,
    get_portfolio_data,
    missing_data,
    update_account,
)
from portfolio_manager.dashboard.portfolio_data import AllocationRiskLabels, PortfolioData
from portfolio_manager.market_data.yahoo import YFinHistCols, YFinInfoCols
from portfolio_manager.storage.queries import query_yahoo_finance_prod_info, search_yahoo_finance_instruments
from portfolio_manager.utils.logs import hide_secrets

logger = logging.getLogger(__name__)

NO_DATA_HINT = 'press Update (downloads the Degiro data, a few minutes)'


# DASHBOARD
def build_content_portfolio(account: Account):
    """Layout only: the figures are filled by the render callbacks, which also run when the page is loaded."""
    return html.Div(
        dbc.Card(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dcc.Dropdown(
                                    [acc.name for acc in list_accounts()],
                                    account.name,
                                    id='dropdown-portfolio',
                                    clearable=False,
                                )
                            ],
                            style={'width': '15%'},
                        ),
                        dbc.Col(
                            [dbc.Button('Update', id='button-update', className='me-2', n_clicks=0)],
                            style={'width': '10%'},
                        ),
                        dbc.Col([loading_wrapper(html.Div(id='update-status'))], style={'width': '10%'}),
                        dbc.Col([], style={'width': '10%'}),
                    ],
                    align='center',
                ),
                html.Div(id='portfolio-message'),
                # the figures, hidden while the account has no data
                html.Div(
                    id='portfolio-figures',
                    children=[
                        html.Br(),
                        dbc.Row(
                            [
                                dbc.Col(
                                    loading_wrapper(card_wrapper(dcc.Graph(id='fig_navs', figure=get_fig_empty()))),
                                    style={'width': '10%'},
                                ),
                                dbc.Col(
                                    loading_wrapper(card_wrapper(dcc.Graph(id='fig_comp', figure=get_fig_empty()))),
                                    style={'width': '10%'},
                                ),
                                dbc.Col(
                                    loading_wrapper(card_wrapper(dcc.Graph(id='fig_perf', figure=get_fig_empty()))),
                                    style={'width': '10%'},
                                ),
                                dbc.Col(
                                    loading_wrapper(card_wrapper(dcc.Graph(id='fig_corr', figure=get_fig_empty()))),
                                    style={'width': '10%'},
                                ),
                            ],
                            align='center',
                        ),
                        html.Br(),
                        dbc.Row(
                            [
                                dbc.Col(
                                    loading_wrapper(
                                        card_wrapper(dcc.Graph(id='fig_pf_hist_sharpe', figure=get_fig_empty()))
                                    ),
                                    style={'width': '10%'},
                                ),
                                dbc.Col(
                                    loading_wrapper(
                                        card_wrapper(dcc.Graph(id='fig_risk_contrib', figure=get_fig_empty()))
                                    ),
                                    style={'width': '10%'},
                                ),
                                dbc.Col(
                                    loading_wrapper(
                                        card_wrapper(dcc.Graph(id='fig_pf_instr_adj_close', figure=get_fig_empty()))
                                    ),
                                    style={'width': '10%'},
                                ),
                                dbc.Col(
                                    loading_wrapper(
                                        card_wrapper(dcc.Graph(id='fig_monthly_ret', figure=get_fig_empty()))
                                    ),
                                    style={'width': '10%'},
                                ),
                            ],
                            align='center',
                        ),
                        html.Br(),
                        dbc.Row(
                            [
                                dbc.Col(
                                    [
                                        # options are filled while typing from the instruments in the database
                                        dbc.Row(
                                            [
                                                dcc.Dropdown(
                                                    id='dropdown_instr',
                                                    options=[],
                                                    placeholder='Enter ticker, ISIN or name...',
                                                    searchable=True,
                                                    clearable=True,
                                                ),
                                                loading_wrapper(
                                                    dcc.Graph(
                                                        id='fig_instr_adj_close', figure=get_fig_instr_adj_close()
                                                    )
                                                ),
                                            ],
                                            align='center',
                                        )
                                    ],
                                    style={'width': '15%'},
                                ),
                            ],
                            align='center',
                        ),
                        html.Br(),
                    ],
                ),
            ],
            body=True,
            color='dark',
        ),
    )


# NAV ADJUSTED LINE PLOT
def get_fig_navs(pf_data: PortfolioData, bm_data: PortfolioBacktestData) -> go.Figure:
    """Effective NAV of the portfolio and of its benchmark."""
    fig_navs = go.Figure(
        data=[
            go.Scatter(
                x=pf_data.hist_data.nav_eff.index,
                y=pf_data.hist_data.nav_eff.values,
                name='Portfolio',
                mode='lines',
                hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
            )
        ],
        layout=go.Layout(
            xaxis_title=dict(text='Date'),
            yaxis_title=dict(text='NAV (adjusted)'),
            template=LAYOUT_TEMPLATE,
            legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1),
        ),
    )
    fig_navs.add_trace(
        go.Scatter(
            x=bm_data.nav_eff.index,
            y=bm_data.nav_eff.values,
            name='Benchmark',
            mode='lines',
            hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
        )
    )
    return fig_navs


# PORTFOLIO ALLOCATION PIE CHART
def get_fig_allocation(pf_data: PortfolioData) -> go.Figure:
    """Latest weights of the instruments and cash."""
    return go.Figure(
        data=[
            go.Pie(
                labels=pf_data.alloc_risk_df[AllocationRiskLabels.SYMBOL],
                values=pf_data.alloc_risk_df[AllocationRiskLabels.ALLOCATION],
                customdata=pf_data.alloc_risk_df[[AllocationRiskLabels.NAME]],
                hovertemplate='%{customdata[0]}<br>%{value:.2%}<br><extra></extra>',
                direction='clockwise',
                hole=0.4,
                sort=False,
            )
        ],
        layout=go.Layout(
            title=dict(text='Portfolio allocation'), legend=dict(orientation='h', y=-0.1), template=LAYOUT_TEMPLATE
        ),
    )


# ASSET CORRELATION MATRIX HEATMAP
def get_fig_corr(pf_data: PortfolioData) -> go.Figure:
    """Correlation matrix of the weekly returns of the instruments."""
    corr_df = compute_corr_mat(pf_data.returns_adj_weekly_df)
    col_name_dct = dict(pf_data.prod_df['symbol'])
    labels = corr_df.rename(columns=col_name_dct).columns.to_list()
    return go.Figure(
        data=[
            go.Heatmap(
                z=corr_df.values,
                x=labels,
                y=list(reversed(labels)),
                colorscale='RdBu_r',
                zmin=-1,
                zmax=1,
                xgap=1,
                ygap=1,
                hoverongaps=False,
            )
        ],
        layout=go.Layout(
            title=dict(text='Asset correlation matrix'),
            template=LAYOUT_TEMPLATE,
            xaxis=dict(side='top', scaleanchor='y', constrain='domain'),
            yaxis=dict(scaleanchor='x', constrain='domain'),
        ),
    )


# RISK ALLOCATION PIE CHART
def get_fig_risk_contrib(pf_data: PortfolioData) -> go.Figure:
    """Contribution of each instrument to the portfolio risk."""
    return go.Figure(
        data=[
            go.Pie(
                labels=pf_data.alloc_risk_df[AllocationRiskLabels.SYMBOL],
                values=pf_data.alloc_risk_df[AllocationRiskLabels.RISK_CONTRIB],
                customdata=pf_data.alloc_risk_df[[AllocationRiskLabels.NAME]],
                hovertemplate='%{customdata[0]}<br>%{value:.2%}<br><extra></extra>',
                direction='clockwise',
                hole=0.4,
                sort=False,
            )
        ],
        layout=go.Layout(
            title=dict(text='Risk allocation'), legend=dict(orientation='h', y=-0.1), template=LAYOUT_TEMPLATE
        ),
    )


# PERFORMANCE METRICS TABLE
def get_fig_perf(pf_data: PortfolioData) -> go.Figure:
    """Performance metrics of the portfolio and of its benchmark."""
    return get_fig_metrics_table(
        {
            'Portfolio': pf_data.perf_dct[PerfDataTabs.RISK_METRICS],
            'Benchmark': pf_data.perf_bm_dct[PerfDataTabs.RISK_METRICS],
        }
    )


# HISTORICAL SHARPE RATIO LINE PLOT
def get_fig_hist_sharpe(pf_data: PortfolioData) -> go.Figure:
    """Sharpe ratio over time of the portfolio and of its benchmark."""
    hist_perf_df = pf_data.perf_dct[PerfDataTabs.HIST_PERF_METRICS]
    fig_sharpe = go.Figure(
        data=[
            go.Scatter(
                x=hist_perf_df.index,
                y=hist_perf_df[Metrics.SHARPE_RATIO.name],
                name='Portfolio',
                mode='lines',
                hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
            )
        ],
        layout=go.Layout(
            xaxis_title=dict(text='Date'),
            yaxis_title=dict(text=Metrics.SHARPE_RATIO.name),
            template=LAYOUT_TEMPLATE,
            legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1),
        ),
    )
    hist_perf_bm_df = pf_data.perf_bm_dct[PerfDataTabs.HIST_PERF_METRICS]
    fig_sharpe.add_trace(
        go.Scatter(
            x=hist_perf_bm_df.index,
            y=hist_perf_bm_df[Metrics.SHARPE_RATIO.name].values,
            name='Benchmark',
            mode='lines',
            hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
        )
    )
    return fig_sharpe


# MONTHLY RETURNS HISTOGRAM CHART
def get_fig_monthly_ret(pf_data: PortfolioData) -> go.Figure:
    """Distribution of the monthly returns."""
    return go.Figure(
        data=[
            go.Histogram(
                x=pf_data.perf_dct[PerfDataTabs.RETURNS_MONTHLY]['return'].values, histnorm='probability', name='return'
            )
        ],
        layout=go.Layout(
            title=dict(text='Monthly returns distribution'),
            xaxis_title=dict(text='Return'),
            yaxis_title=dict(text='Frequency'),
            bargap=0.1,
            template=LAYOUT_TEMPLATE,
        ),
    )


# PORTFOLIO INSTRUMENTS ADJUSTED CLOSE
def get_fig_pf_instr_adj_close(
    pf_data: PortfolioData, currency: str, is_visible: list[bool] | None = None
) -> go.Figure:
    """With some instruments hidden, the visible ones are rebased to 100 to make them comparable."""
    instruments = pf_data.alloc_risk_df.index.drop('Cash')
    if is_visible is None:
        is_visible = [True] * len(instruments)
    close_adj_df = pf_data.hist_data.close_adj.dropna(how='all')
    rebase = not all(is_visible)
    if rebase:
        visible_df = pf_data.hist_data.close_adj[[c for c, v in zip(instruments, is_visible, strict=True) if v]]
        visible_df = visible_df.ffill().dropna(how='all')
        visible_df = visible_df.apply(lambda x: x.div(x.dropna().iloc[0]).mul(100))
    y_label = 'Adjusted Closing Price (rebased to 100)' if rebase else f'Adjusted Closing Price [{currency}]'
    fig_pf_instr_adj_close = go.Figure(
        layout=go.Layout(xaxis_title=dict(text='Date'), yaxis_title=dict(text=y_label), template=LAYOUT_TEMPLATE)
    )
    col_name_dct = dict(pf_data.prod_df['symbol'])
    for col, visible in zip(instruments, is_visible, strict=True):
        ser = visible_df[col] if rebase and visible else close_adj_df[col].ffill()
        fig_pf_instr_adj_close.add_trace(
            go.Scatter(
                x=ser.index,
                y=ser,
                name=col_name_dct[col],
                visible=True if visible else 'legendonly',
                mode='lines',
                hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
            )
        )
    return fig_pf_instr_adj_close


def get_visibility_after_restyle(figure: dict | None, restyle_data: list | None) -> list[bool] | None:
    """Visibility of each trace after a legend click, starting from the figure currently shown: restyle_data is
    [{'visible': [values]}, [trace indices]], listing only the traces that changed.
    """
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
def get_fig_instr_adj_close(title: str | None = None) -> go.Figure:
    """Empty chart of the instrument search, with an optional message as title."""
    return go.Figure(
        layout=go.Layout(
            xaxis_title=dict(text='Date'),
            yaxis_title=dict(text='Adjusted Closing Price'),
            template=LAYOUT_TEMPLATE,
            title=title,
        )
    )


def with_ui_revision(fig: go.Figure, account_name: str) -> go.Figure:
    """Keep zoom and legend state across redraws of the same account."""
    return fig.update_layout(uirevision=account_name)


# remember the selected account in the browser (shared with the Strategies page)
@callback(
    Output('store-account', 'data', allow_duplicate=True),
    Input('dropdown-portfolio', 'value'),
    prevent_initial_call=True,
)
def select_account(account_name: str) -> str:
    """Remember the account selected on the Portfolio page."""
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
    Output('portfolio-message', 'children'),
    Output('portfolio-figures', 'style'),
    Output('button-update', 'disabled'),
    Input('dropdown-portfolio', 'value'),
    Input('store-data-version', 'data'),
)
def render_portfolio(account_name: str, data_version) -> tuple:
    """Draw the figures of the Portfolio page from the saved data of the account; without data, a message instead
    (Update is disabled until the account is connected).
    """
    account = get_account(account_name)
    missing = missing_data(account, NO_DATA_HINT)
    if missing is not None:
        not_connected = account is None or account.currency is None
        return (
            *([get_fig_empty()] * 7),
            dbc.Alert(missing, color='info', className='mt-4'),
            {'display': 'none'},
            not_connected,
        )
    pf_data = get_portfolio_data(account)
    bm_data = get_benchmark_data(account)
    figs = (
        get_fig_navs(pf_data, bm_data),
        get_fig_allocation(pf_data),
        get_fig_perf(pf_data),
        get_fig_corr(pf_data),
        get_fig_hist_sharpe(pf_data),
        get_fig_risk_contrib(pf_data),
        get_fig_monthly_ret(pf_data),
    )
    return *(with_ui_revision(fig, account_name) for fig in figs), None, {}, False


# draw the portfolio instruments chart; a legend click rebases the visible instruments
@callback(
    Output('fig_pf_instr_adj_close', 'figure'),
    Input('dropdown-portfolio', 'value'),
    Input('store-data-version', 'data'),
    Input('fig_pf_instr_adj_close', 'restyleData'),
    State('fig_pf_instr_adj_close', 'figure'),
)
def render_pf_instr_adj_close(account_name: str, data_version, restyle_data, figure) -> go.Figure:
    """Draw the adjusted prices of the instruments held; clicks on the legend rebase the visible ones."""
    account = get_account(account_name)
    if missing_data(account, NO_DATA_HINT) is not None:
        return get_fig_empty()
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
    prevent_initial_call=True,
)
def run_update(n_clicks, account_name: str) -> tuple:
    """Update the data of the account (Update button); the new data version redraws the figures."""
    # Dash also calls this when the page is built (prevent_initial_call does not apply, since
    # store-data-version is outside the page): update only on an actual click
    if not n_clicks:
        raise PreventUpdate
    try:
        update_account(get_account(account_name))
    except Exception as e:  # any download or computation problem: shown next to the button, details in the log
        logger.exception('Update of %s failed', account_name)
        return no_update, html.Span(f'Not updated: {hide_secrets(str(e))}', className='text-danger')
    return time.time(), f'Updated {time.strftime("%H:%M")}'


# list the instruments matching the typed text as "{ticker} - {name}" (database only, no download)
@callback(
    Output('dropdown_instr', 'options'),
    Input('dropdown_instr', 'search_value'),
    State('dropdown_instr', 'value'),
    State('dropdown_instr', 'options'),
    prevent_initial_call=True,
)
def search_instruments(search_value: str | None, value: str | None, options: list | None) -> list:
    """List the instruments matching the typed text as "{ticker} - {name}"."""
    if not search_value or not search_value.strip():
        raise PreventUpdate  # keep the current options, so that the selected instrument stays displayed
    results_df = search_yahoo_finance_instruments(text=search_value)
    new_options = [
        {
            'label': f'{r.ticker} - {r.name}' if isinstance(r.name, str) and r.name else r.ticker,
            'value': r.ticker,
            # the dropdown also filters the options while typing: let it match ticker, ISIN and name
            'search': ' '.join(str(v) for v in (r.ticker, r.isin, r.name) if isinstance(v, str)),
        }
        for r in results_df.itertuples()
    ]
    # the selected instrument must stay among the options to remain displayed
    if value is not None and value not in [o['value'] for o in new_options]:
        new_options += [o for o in (options or []) if o['value'] == value]
    return new_options


# update instrument chart with the selected instrument
@callback(
    Output('fig_instr_adj_close', 'figure'),
    Input('dropdown_instr', 'value'),
    State('dropdown-portfolio', 'value'),
    prevent_initial_call=True,
)
def update_instr_adj_close_fig(ticker: str | None, account_name: str) -> go.Figure:
    """Chart of the adjusted prices of the instrument picked in the search."""
    if not ticker:  # cleared selection
        return get_fig_instr_adj_close()
    account = get_account(account_name)
    results_df = query_yahoo_finance_prod_info(ticker=ticker)
    if ticker not in results_df.columns:
        return get_fig_instr_adj_close(title=f'No instrument found for "{ticker}"')
    info = results_df[ticker]
    name = info.get(YFinInfoCols.name_long.value) or info.get(YFinInfoCols.name_short.value) or ticker
    currency = info[YFinInfoCols.currency.value]
    # download only the selected listing (by ticker, not by a search returning several listings)
    df = fetch_instr_hist_data(isin_lst=[None], columns=YFinHistCols.adj_close, ticker_lst=ticker, name_lst=name)[
        YFinHistCols.adj_close
    ]
    # Yahoo Finance may return no usable history for the matched instrument
    df = df.dropna(axis=1, how='all')
    if df.empty:
        return get_fig_instr_adj_close(title=f'No price history found for {name} ({ticker})')
    df_base = prices_to_base_curr(account=account, price_df=df, curr_info=[currency])
    fig = go.Figure(
        layout=go.Layout(
            xaxis_title=dict(text='Date'),
            yaxis_title=dict(text='Adjusted Closing Price'),
            template=LAYOUT_TEMPLATE,
            title=name,
            showlegend=True,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df.iloc[:, 0],
            name=f'{df.columns[0]} [{currency}]',
            mode='lines',
            hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
        )
    )
    if not df_base.empty:
        fig.add_trace(
            go.Scatter(
                x=df_base.index,
                y=df_base.iloc[:, 0],
                name=f'{df.columns[0]} [{account.currency}]',
                mode='lines',
                hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
            )
        )
    return fig
