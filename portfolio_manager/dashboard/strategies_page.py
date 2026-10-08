"""Strategies page: optimization settings, optimized portfolios."""

import time
from datetime import datetime

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate

from portfolio_manager.analytics.metrics import PerfDataTabs
from portfolio_manager.config.accounts import Accounts
from portfolio_manager.dashboard.common import (
    LAYOUT_TEMPLATE,
    card_wrapper,
    get_fig_empty,
    get_fig_metrics_table,
    loading_wrapper,
)
from portfolio_manager.dashboard.data_service import (
    OptimizedData,
    get_optimized_data,
    get_portfolio_data,
    run_optimization,
)
from portfolio_manager.dashboard.portfolio_data import PortfolioData
from portfolio_manager.optimization.settings import (
    ALLOCATION_STRATS_LABELS,
    OPTIMIZATION_FREQ_LABELS,
    AllocationStrats,
    OptimizationSettings,
)

PCT_MARKS = {v: f'{v}%' for v in range(0, 101, 10)}
# controls of the optimization settings, in the order of the callbacks' outputs and states
SETTINGS_CONTROLS = [
    ('strat-method', 'value'),
    ('strat-freq', 'value'),
    ('strat-max-assets', 'value'),
    ('strat-weight-range', 'value'),
    ('strat-min-position', 'value'),
    ('strat-invested-range', 'value'),
    ('strat-max-vol', 'value'),
    ('strat-target-vol', 'value'),
]


def labelled(label: str, control, width: int) -> dbc.Col:
    """Column with a control and its label."""
    return dbc.Col([dbc.Label(label, className='small mb-1'), control], width=width)


def pct_input(control_id: str, placeholder: str) -> dbc.InputGroup:
    """Numeric input in percent."""
    return dbc.InputGroup(
        [
            dbc.Input(id=control_id, type='number', min=0, max=100, step=0.5, placeholder=placeholder, size='sm'),
            dbc.InputGroupText('%'),
        ],
        size='sm',
    )


# OPTIMIZATION SETTINGS
def get_settings_card() -> dbc.Card:
    """Control values are filled from the saved optimization of the selected account."""
    return dbc.Card(
        dbc.CardBody(
            [
                html.H6('Optimization settings', className='mb-2'),
                dbc.Row(
                    [
                        labelled(
                            'Method',
                            dcc.Dropdown(
                                [{'label': v, 'value': k.value} for k, v in ALLOCATION_STRATS_LABELS.items()],
                                id='strat-method',
                                clearable=False,
                            ),
                            width=3,
                        ),
                        labelled(
                            'Rebalancing interval',
                            dcc.Dropdown(
                                [{'label': v, 'value': k} for k, v in OPTIMIZATION_FREQ_LABELS.items()],
                                id='strat-freq',
                                clearable=False,
                            ),
                            width=2,
                        ),
                        labelled(
                            'Max assets',
                            dbc.Input(
                                id='strat-max-assets', type='number', min=1, step=1, placeholder='no limit', size='sm'
                            ),
                            width=2,
                        ),
                        labelled('Max volatility (Max Sharpe)', pct_input('strat-max-vol', 'no limit'), width=2),
                        labelled('Target volatility (Max return)', pct_input('strat-target-vol', 'none'), width=3),
                    ],
                    className='mb-2',
                ),
                dbc.Row(
                    [
                        labelled(
                            'Weight per asset',
                            dcc.RangeSlider(
                                0, 100, 1, id='strat-weight-range', marks=PCT_MARKS, tooltip={'placement': 'bottom'}
                            ),
                            width=5,
                        ),
                        labelled(
                            'Min position size (smaller ones dropped)',
                            pct_input('strat-min-position', '0 = off'),
                            width=2,
                        ),
                        labelled(
                            'Total invested (rest in cash)',
                            dcc.RangeSlider(
                                0, 100, 1, id='strat-invested-range', marks=PCT_MARKS, tooltip={'placement': 'bottom'}
                            ),
                            width=5,
                        ),
                    ]
                ),
            ]
        ),
        color='black',
        className='mb-4',
    )


# DASHBOARD
def build_content_strategies(account: Accounts):
    """Layout only: the figures and the settings are filled by callbacks, which also run when the page is loaded."""
    return html.Div(
        dbc.Card(
            [
                # the status takes the width left by the dropdown and the button
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dcc.Dropdown(
                                    [acc.name for acc in Accounts],
                                    account.name,
                                    id='dropdown-strategies',
                                    clearable=False,
                                )
                            ],
                            width=4,
                        ),
                        dbc.Col(
                            [dbc.Button('Run optimization', id='button-optimize', className='me-2', n_clicks=0)],
                            width='auto',
                        ),
                        dbc.Col([loading_wrapper(html.Div(id='optimize-status'))]),
                    ],
                    align='center',
                    className='mb-4',
                ),
                # same space above and below the settings card
                get_settings_card(),
                dbc.Row(
                    [
                        dbc.Col(
                            loading_wrapper(card_wrapper(dcc.Graph(id='fig_strat_navs', figure=get_fig_empty()))),
                            width=8,
                        ),
                        dbc.Col(
                            loading_wrapper(card_wrapper(dcc.Graph(id='fig_strat_perf', figure=get_fig_empty()))),
                            width=4,
                        ),
                    ],
                    align='center',
                ),
                html.Br(),
            ],
            body=True,
            color='dark',
        ),
    )


def settings_to_controls(settings: OptimizationSettings) -> tuple:
    """Values of the settings controls (percentages) from optimization settings."""

    def pct(v):
        return None if v is None else round(100 * v, 2)

    return (
        settings.method.value,
        settings.optimization_freq,
        settings.max_asset_num,
        [pct(settings.min_asset_exposure), pct(settings.max_asset_exposure)],
        pct(settings.min_position_size),
        [pct(settings.min_pf_exposure), pct(settings.max_pf_exposure)],
        pct(settings.max_vol),
        pct(settings.target_vol),
    )


def controls_to_settings(
    method, freq, max_assets, weight_range, min_position, invested_range, max_vol, target_vol
) -> OptimizationSettings:
    """Optimization settings from the values of the settings controls (percentages)."""

    def frac(v):
        return None if v in (None, '') else float(v) / 100.0

    return OptimizationSettings(
        method=AllocationStrats(method),
        optimization_freq=freq,
        min_asset_exposure=frac(weight_range[0]),
        max_asset_exposure=frac(weight_range[1]),
        min_position_size=frac(min_position) or 0.0,
        min_pf_exposure=frac(invested_range[0]),
        max_pf_exposure=frac(invested_range[1]),
        max_vol=frac(max_vol),
        target_vol=frac(target_vol),
        max_asset_num=None if max_assets in (None, '') else int(max_assets),
    )


# NAV ADJUSTED LINE PLOT: portfolio vs optimized portfolio
def get_fig_strat_navs(account: Accounts, pf_data: PortfolioData, opt_data: OptimizedData | None) -> go.Figure:
    """Effective NAV of the portfolio and of its optimized version, with the settings."""
    fig_navs = go.Figure(
        data=[
            go.Scatter(
                x=pf_data.hist_data.nav_eff.index,
                y=pf_data.hist_data.nav_eff.values,
                name=pf_data.hist_data.name,
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
    if opt_data is None:
        fig_navs.update_layout(
            title=dict(text=f'No optimization computed yet for {account.name}: press "Run optimization"')
        )
        return fig_navs
    fig_navs.add_trace(
        go.Scatter(
            x=opt_data.hist_data.nav_eff.index,
            y=opt_data.hist_data.nav_eff.values,
            name=f'{account.name} Opt. ({ALLOCATION_STRATS_LABELS[opt_data.settings.method]})',
            mode='lines',
            hovertemplate='%{x|%Y/%m/%d}: %{y}<extra></extra>',
        )
    )
    last_opt = opt_data.hist_data.nav_eff.index.max()
    title = (
        f'Optimization computed on {datetime.fromtimestamp(opt_data.computed_at):%Y-%m-%d %H:%M}, '
        f'data until {last_opt:%Y-%m-%d}<br><sup>Settings: {opt_data.settings.describe()}</sup>'
    )
    if pf_data.hist_data.nav_eff.index.max() > last_opt:
        title += '<br><sup>Portfolio data is more recent: run the optimization again to include it</sup>'
    fig_navs.update_layout(title=dict(text=title), margin=dict(t=110))
    return fig_navs


# PERFORMANCE METRICS TABLE: portfolio vs optimized portfolio
def get_fig_strat_perf(pf_data: PortfolioData, opt_data: OptimizedData | None) -> go.Figure:
    """Performance metrics of the portfolio and of its optimized version."""
    opt_metrics = None
    if opt_data is not None and opt_data.perf_dct is not None:
        opt_metrics = opt_data.perf_dct.get(PerfDataTabs.RISK_METRICS)
    return get_fig_metrics_table({'Portfolio': pf_data.perf_dct[PerfDataTabs.RISK_METRICS], 'Optimized': opt_metrics})


# remember the selected account in the browser (shared with the Portfolio page)
@callback(
    Output('store-account', 'data', allow_duplicate=True),
    Input('dropdown-strategies', 'value'),
    prevent_initial_call=True,
)
def select_account_strategies(account_name: str) -> str:
    """Remember the account selected on the Strategies page."""
    return account_name


# show the settings of the saved optimization of the selected account (defaults if there is none)
@callback(
    *[Output(i, p) for i, p in SETTINGS_CONTROLS],
    Input('dropdown-strategies', 'value'),
)
def load_settings(account_name: str) -> tuple:
    """Show the settings of the saved optimization of the account (defaults if there is none)."""
    opt_data = get_optimized_data(Accounts.get_account_by_name(name=account_name))
    return settings_to_controls(opt_data.settings if opt_data is not None else OptimizationSettings())


# volatility settings only apply to their method; equal weights give no ranking to pick assets or positions from
@callback(
    Output('strat-max-vol', 'disabled'),
    Output('strat-target-vol', 'disabled'),
    Output('strat-max-assets', 'disabled'),
    Output('strat-min-position', 'disabled'),
    Input('strat-method', 'value'),
)
def enable_method_settings(method: str) -> tuple:
    """Grey out the settings that do not apply to the method: (max vol, target vol, max assets, min position)."""
    equal_weight = method == AllocationStrats.EQUAL_WEIGHT.value
    return (
        method != AllocationStrats.MAX_SHARPE.value,
        method != AllocationStrats.MAX_RET.value,
        equal_weight,
        equal_weight,
    )


# draw the strategies figures from saved data only (also when the page is loaded and after an optimization)
@callback(
    Output('fig_strat_navs', 'figure'),
    Output('fig_strat_perf', 'figure'),
    Input('dropdown-strategies', 'value'),
    Input('store-strategy-version', 'data'),
)
def render_strategies(account_name: str, strategy_version) -> tuple:
    """Draw the figures of the Strategies page from the saved data."""
    account = Accounts.get_account_by_name(name=account_name)
    pf_data = get_portfolio_data(account)
    opt_data = get_optimized_data(account)
    figs = (get_fig_strat_navs(account, pf_data, opt_data), get_fig_strat_perf(pf_data, opt_data))
    # keep zoom and legend state across redraws of the same account
    return tuple(fig.update_layout(uirevision=account_name) for fig in figs)


# run the optimization of the selected account with the chosen settings; the new version triggers the redraw
@callback(
    Output('store-strategy-version', 'data'),
    Output('optimize-status', 'children'),
    Input('button-optimize', 'n_clicks'),
    State('dropdown-strategies', 'value'),
    *[State(i, p) for i, p in SETTINGS_CONTROLS],
    prevent_initial_call=True,
)
def run_optimization_callback(n_clicks, account_name: str, *controls) -> tuple:
    """Run the optimization with the chosen settings (Run optimization button); the new version redraws the figures."""
    # Dash also calls this when the page is built (prevent_initial_call does not apply, since
    # store-strategy-version is outside the page): optimize only on an actual click
    if not n_clicks:
        raise PreventUpdate
    try:
        settings = controls_to_settings(*controls)
        summary = run_optimization(Accounts.get_account_by_name(name=account_name), settings=settings)
    except ValueError as e:
        return no_update, html.Span(f'Not run: {e}', className='text-danger')
    return time.time(), f'Optimized {time.strftime("%H:%M")} ({summary})'
