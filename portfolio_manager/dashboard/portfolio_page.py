"""Portfolio page: layout, figures and callbacks."""

import logging
import time

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate

from portfolio_manager.analytics.instruments import prices_to_base_curr
from portfolio_manager.analytics.metrics import CUM_TWR_LABEL, PerfDataTabs
from portfolio_manager.backtest.portfolio import PortfolioBacktestData
from portfolio_manager.config.accounts import Account, get_account, list_accounts
from portfolio_manager.dashboard.common import (
    LAYOUT_TEMPLATE,
    card_wrapper,
    compute_corr_mat,
    get_fig_empty,
    loading_wrapper,
    metrics_table,
)
from portfolio_manager.dashboard.data_service import (
    available_update,
    get_benchmark_data,
    get_portfolio_data,
    missing_data,
    update_account,
)
from portfolio_manager.dashboard.portfolio_data import UNKNOWN, AllocationRiskLabels, PortfolioData
from portfolio_manager.market_data.history import listing_currencies, load_adjusted_closes, refresh_history
from portfolio_manager.market_data.yahoo import is_yahoo_reachable
from portfolio_manager.storage.queries import query_yahoo_instruments, search_yahoo_finance_instruments
from portfolio_manager.utils.logs import hide_secrets

logger = logging.getLogger(__name__)

NO_DATA_HINT = 'press Update (downloads the Degiro data, a few minutes)'
UPDATE_COLOR = 'primary'  # Update button: nothing new to download
UPDATE_AVAILABLE_COLOR = 'warning'  # Update button: the prices of a later trading day are final
# legend of the pie charts on their right: the pie keeps its size whatever the length of the legend
PIE_LEGEND = dict(orientation='v', x=1.02, xanchor='left', y=0.5, yanchor='middle')


# DASHBOARD
def figure_card(graph_id: str, position: str) -> html.Div:
    """A figure in its card, at its place in the grid of the page (position: its class in assets/portfolio.css)."""
    graph = dcc.Graph(id=graph_id, figure=get_fig_empty())
    return html.Div(loading_wrapper(card_wrapper(graph)), className=f'pf-card {position}')


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
                    # two cards per row, four on wide screens (assets/portfolio.css)
                    className='pf-grid',
                    children=[
                        figure_card('fig_navs', 'pf-nav'),
                        html.Div(
                            loading_wrapper(card_wrapper(html.Div(id='table_perf'))), className='pf-card pf-metrics'
                        ),
                        figure_card('fig_comp', 'pf-alloc'),
                        figure_card('fig_risk_contrib', 'pf-risk'),
                        figure_card('fig_geography', 'pf-geo'),
                        figure_card('fig_asset_classes', 'pf-classes'),
                        figure_card('fig_corr', 'pf-corr'),
                        html.Div(
                            card_wrapper(
                                [
                                    # options are filled while typing from the instruments in the database
                                    dcc.Dropdown(
                                        id='dropdown_instr',
                                        options=[],
                                        placeholder='Enter ticker, ISIN or name...',
                                        searchable=True,
                                        clearable=True,
                                    ),
                                    loading_wrapper(
                                        dcc.Graph(id='fig_instr_adj_close', figure=get_fig_instr_adj_close())
                                    ),
                                ]
                            ),
                            className='pf-card pf-search',
                        ),
                    ],
                ),
            ],
            body=True,
            color='dark',
        ),
    )


# CUMULATIVE TIME-WEIGHTED RETURN LINE PLOT
def get_fig_cum_time_weighted_returns(pf_data: PortfolioData, bm_data: PortfolioBacktestData) -> go.Figure:
    """Cumulative time-weighted return of the portfolio (without the effect of deposits and withdrawals) and of its
    benchmark, from the first day.
    """
    fig = go.Figure(
        layout=go.Layout(
            xaxis_title=dict(text='Date'),
            yaxis=dict(title=dict(text=CUM_TWR_LABEL), tickformat='.0%'),
            template=LAYOUT_TEMPLATE,
            legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1),
        ),
    )
    for label, data in [('Portfolio', pf_data.hist_data), ('Benchmark', bm_data)]:
        returns = data.cum_time_weighted_return
        fig.add_trace(
            go.Scatter(
                x=returns.index,
                y=returns.values,
                name=label,
                mode='lines',
                hovertemplate='%{x|%Y/%m/%d}: %{y:.2%}<extra></extra>',
            )
        )
    return fig


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
        layout=go.Layout(title=dict(text='Portfolio allocation'), legend=PIE_LEGEND, template=LAYOUT_TEMPLATE),
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
        layout=go.Layout(title=dict(text='Risk allocation'), legend=PIE_LEGEND, template=LAYOUT_TEMPLATE),
    )


# GEOGRAPHY AND ASSET CLASSES PIE CHARTS
UNKNOWN_COLOR = '#7f7f7f'  # the Unknown slice, grey


def get_fig_breakdown(table: pd.DataFrame, title: str) -> go.Figure:
    """Weights by region or asset class (see portfolio_data.breakdown), with the instruments of each slice."""
    colors = [
        UNKNOWN_COLOR if label == UNKNOWN else px.colors.qualitative.Plotly[n % len(px.colors.qualitative.Plotly)]
        for n, label in enumerate(table.index)
    ]
    return go.Figure(
        data=[
            go.Pie(
                labels=table.index,
                values=table['weight'],
                customdata=table[['symbols']],
                hovertemplate='%{label}<br>%{value:.2%}<br>%{customdata[0]}<extra></extra>',
                marker=dict(colors=colors),
                direction='clockwise',
                hole=0.4,
                sort=False,
            )
        ],
        layout=go.Layout(title=dict(text=title), legend=PIE_LEGEND, template=LAYOUT_TEMPLATE),
    )


# PERFORMANCE METRICS TABLE
def get_table_perf(pf_data: PortfolioData) -> html.Table:
    """Performance metrics of the portfolio and of its benchmark."""
    return metrics_table(
        {
            'Portfolio': pf_data.perf_dct[PerfDataTabs.RISK_METRICS],
            'Benchmark': pf_data.perf_bm_dct[PerfDataTabs.RISK_METRICS],
        },
        currency=pf_data.account.currency,
    )


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


# remember the selected account in the browser (shared with the other pages)
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
    Output('fig_corr', 'figure'),
    Output('fig_risk_contrib', 'figure'),
    Output('fig_geography', 'figure'),
    Output('fig_asset_classes', 'figure'),
    Output('table_perf', 'children'),
    Output('portfolio-message', 'children'),
    Output('portfolio-figures', 'style'),
    Output('button-update', 'disabled'),
    Output('button-update', 'color'),
    Output('button-update', 'title'),
    Input('dropdown-portfolio', 'value'),
    Input('store-data-version', 'data'),
)
def render_portfolio(account_name: str, data_version) -> tuple:
    """Draw the figures of the Portfolio page from the saved data of the account; without data, a message instead
    (Update is disabled until the account is connected). Update is orange when the prices of a trading day became
    final after the last update.
    """
    account = get_account(account_name)
    missing = missing_data(account, NO_DATA_HINT)
    if missing is not None:
        not_connected = account is None or account.currency is None
        return (
            *([get_fig_empty()] * 6),
            None,
            dbc.Alert(missing, color='info', className='mt-4'),
            {'display': 'none'},
            not_connected,
            UPDATE_COLOR,
            None,
        )
    pf_data = get_portfolio_data(account)
    bm_data = get_benchmark_data(account)
    figs = (
        get_fig_cum_time_weighted_returns(pf_data, bm_data),
        get_fig_allocation(pf_data),
        get_fig_corr(pf_data),
        get_fig_risk_contrib(pf_data),
        get_fig_breakdown(pf_data.geography_df, 'Geography (invested part)'),
        get_fig_breakdown(pf_data.asset_classes_df, 'Asset classes'),
    )
    hint = available_update(account)
    color = UPDATE_AVAILABLE_COLOR if hint else UPDATE_COLOR
    figs = (with_ui_revision(fig, account_name) for fig in figs)
    return *figs, get_table_perf(pf_data), None, {}, False, color, hint or 'Up to date'


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
    instruments = query_yahoo_instruments([ticker])
    if instruments.empty:
        return get_fig_instr_adj_close(title=f'No instrument found for "{ticker}"')
    info = instruments.loc[ticker]
    name = next((n for n in (info['name_long'], info['name_short']) if isinstance(n, str) and n), ticker)
    currency = listing_currencies([ticker]).get(ticker, info['currency'])  # corrected if Yahoo reports a wrong one
    # bring the stored history of the selected listing up to date (the whole history the first time)
    if is_yahoo_reachable():
        try:
            refresh_history(ticker)
        except Exception as e:  # any download problem: the stored history is shown
            logger.warning('Yahoo Finance download failed for %s: %s', ticker, e)
    df = load_adjusted_closes([ticker])
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
