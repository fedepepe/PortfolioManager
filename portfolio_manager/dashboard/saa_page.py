"""Strategic Asset Allocation page: an allocation of the account built in four steps (asset class weights, sub-classes
of the classes, instruments of each part with manual or optimized weights, backtest), its summary, and the named
allocations saved for the account.

The allocation being edited is kept in the browser (store saa-allocation, plain data); the steps' controls are drawn
from it when a step is shown or an allocation is loaded, and each change of a control updates it. A step whose content
is complete gets a check mark on its tab.
"""

import logging
import time
from datetime import datetime

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import ALL, MATCH, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from portfolio_manager.analytics.metrics import Metrics
from portfolio_manager.analytics.price_updates import UPDATER, status_text
from portfolio_manager.analytics.saa import split_values
from portfolio_manager.backtest.saa import backtest_allocation
from portfolio_manager.backtest.workflows import account_benchmark, refresh_benchmark
from portfolio_manager.config.accounts import Account, get_account, list_accounts
from portfolio_manager.config.allocations import (
    ASSET_CLASSES,
    PRESETS,
    REBALANCING_FREQ_LABELS,
    Allocation,
    percent_text,
)
from portfolio_manager.dashboard.common import (
    LAYOUT_TEMPLATE,
    card_wrapper,
    get_fig_empty,
    get_fig_metrics_table,
    loading_wrapper,
)
from portfolio_manager.dashboard.saa_data import (
    ALLOCATION_LABEL,
    SPLIT_OPTIONS,
    SavedBacktest,
    benchmark_from_allocation,
    bucket_key,
    can_be_benchmark,
    candidate_rows,
    completed_steps,
    delete_saved_backtest,
    fraction,
    has_saved_backtest,
    new_allocation,
    parse_bucket_key,
    saved_backtest,
    set_bucket_selection,
    set_bucket_weights,
    set_class_targets,
    set_instrument_weights,
    set_split,
    start_text,
    summary_rows,
    warm_up,
    weights_total_class,
)
from portfolio_manager.market_data.freshness import due_listings, latest_final_day, update_listings
from portfolio_manager.optimization.settings import ALLOCATION_STRATS_LABELS, OPTIMIZATION_FREQ_LABELS, AllocationStrats
from portfolio_manager.storage.queries import (
    delete_allocation,
    list_allocations,
    query_allocation,
    save_allocation,
    save_benchmark,
)

logger = logging.getLogger(__name__)

NEW = '__new__'  # the "new allocation" entry of the allocations dropdown
STEPS = {
    'classes': '1 · Asset classes',
    'splits': '2 · Sub-classes',
    'instruments': '3 · Instruments',
    'backtest': '4 · Backtest',
}
DONE_CLASS = 'saa-done'  # tab label of a complete step: a green check mark after it (assets/saa.css)
NO_SPLIT = 'none'
CLASS_INPUTS = [{'type': 'saa-class', 'index': c} for c in ASSET_CLASSES]
SETTINGS_INPUTS = [
    'saa-mode',
    'saa-rebalancing',
    'saa-method',
    'saa-opt-freq',
    'saa-tolerance',
    'saa-max-weight',
    'saa-max-vol',
    'saa-target-vol',
]
# a grid being updated stays visible, dimmed
LOADING_OVERLAY = {'visibility': 'visible', 'opacity': 0.4}
PCT = "params.value == null ? '' : d3.format('.1%')(params.value)"
CANDIDATE_COLUMNS = [
    {'field': 'ticker', 'headerName': 'Ticker', 'width': 110},
    {'field': 'name', 'headerName': 'Name', 'flex': 1, 'minWidth': 220, 'tooltipField': 'name'},
    {'field': 'currency', 'headerName': 'Curr.', 'width': 75},
    {
        'field': 'traded_value',
        'headerName': 'Traded/day',
        'width': 105,
        'valueFormatter': {'function': "params.value == null ? '' : d3.format('.3s')(params.value)"},
    },
    {
        'headerName': 'Hedged',
        'width': 85,
        'valueGetter': {'function': "params.data.hedged ? (params.data.hedge_currency || 'yes') : ''"},
    },
    {'field': Metrics.LAST_YEAR_RETURN.name, 'headerName': '1Y', 'width': 80, 'valueFormatter': {'function': PCT}},
    {'field': Metrics.ANN_3Y_RETURN.name, 'headerName': '3Y p.a.', 'width': 85, 'valueFormatter': {'function': PCT}},
    {'field': Metrics.VOLATILITY.name, 'headerName': 'Vol.', 'width': 80, 'valueFormatter': {'function': PCT}},
    {
        'field': Metrics.SHARPE_RATIO.name,
        'headerName': 'Sharpe',
        'width': 80,
        'valueFormatter': {'function': "params.value == null ? '' : d3.format('.2f')(params.value)"},
    },
    {'field': Metrics.MAX_DD.name, 'headerName': 'Max DD', 'width': 85, 'valueFormatter': {'function': PCT}},
]


def labelled(label: str, control, width: int | str) -> dbc.Col:
    """Column with a control and its label (on one line)."""
    return dbc.Col([dbc.Label(label, className='small mb-1 text-nowrap'), control], width=width)


def pct_input(
    control_id, value=None, disabled: bool = False, placeholder: str = '', live: bool = False
) -> dbc.InputGroup:
    """Numeric input in percent; a live one reports every keystroke (for totals updated while typing), the others when
    the value is complete (Enter or leaving the field).
    """
    return dbc.InputGroup(
        [
            dbc.Input(
                id=control_id,
                type='number',
                min=0,
                max=100,
                step=0.5,
                value=value,
                disabled=disabled,
                placeholder=placeholder,
                size='sm',
                debounce=not live,
            ),
            dbc.InputGroupText('%'),
        ],
        size='sm',
    )


def pct(fraction_value: float | None) -> float | None:
    """A fraction as a rounded percentage for a control."""
    return None if fraction_value is None else round(100 * fraction_value, 2)


def total_text(total: float, text: str) -> html.Span:
    """A total of weights, green at 100%, red above, yellow below."""
    return html.Span(text, className=weights_total_class(total))


# LAYOUT
def classes_section() -> html.Div:
    """Step 1: a preset and the weight of each asset class."""
    rows = [
        dbc.Row(
            [dbc.Col(dbc.Label(c, className='mb-0'), width=4), dbc.Col(pct_input(CLASS_INPUTS[n], live=True), width=3)],
            className='mb-1',
            align='center',
        )
        for n, c in enumerate(ASSET_CLASSES)
    ]
    return html.Div(
        [
            dbc.Row(
                labelled(
                    'Start from a preset',
                    dcc.Dropdown(list(PRESETS), id='saa-preset', placeholder='Choose a preset', clearable=False),
                    width=4,
                ),
                className='mb-3',
            ),
            *rows,
            html.Div(id='saa-class-total', className='mt-2'),
        ]
    )


def instruments_section() -> html.Div:
    """Step 3: the candidate filters, the weight mode and its settings, and one card per bucket (drawn on demand)."""
    method_options = [{'label': v, 'value': k.value} for k, v in ALLOCATION_STRATS_LABELS.items()]
    constraints = dbc.Card(
        dbc.CardBody(
            [
                html.H6('Constraints', className='mb-2'),
                dbc.Row(
                    [
                        labelled('Asset class tolerance (±)', pct_input('saa-tolerance'), width=3),
                        labelled('Max weight per instrument', pct_input('saa-max-weight', placeholder='no limit'), 3),
                        labelled('Max volatility (Max Sharpe)', pct_input('saa-max-vol', placeholder='no limit'), 3),
                        labelled('Target volatility (Max return)', pct_input('saa-target-vol', placeholder='none'), 3),
                    ]
                ),
            ]
        ),
        color='black',
        className='mt-3',  # the same space as below it, before the cards of the buckets
    )
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Checklist(
                            options=[
                                {'label': 'Show only products traded in portfolio currency', 'value': 'currency'},
                                {'label': 'Show leveraged and inverse products', 'value': 'leveraged'},
                            ],
                            value=['currency'],
                            id='saa-filters',
                            switch=True,
                            className='saa-switches',
                        ),
                        width=6,
                    ),
                    labelled(
                        'Weights',
                        dbc.RadioItems(
                            options=[
                                {'label': 'Manual', 'value': 'manual'},
                                {'label': 'Optimized', 'value': 'optimize'},
                            ],
                            id='saa-mode',
                            inline=True,
                        ),
                        width=3,
                    ),
                ],
                className='mb-2',
            ),
            dbc.Row(
                [
                    labelled(
                        'Rebalancing',
                        dcc.Dropdown(
                            [{'label': v, 'value': k} for k, v in REBALANCING_FREQ_LABELS.items()],
                            id='saa-rebalancing',
                            clearable=False,
                        ),
                        width=2,
                    ),
                ],
                id='saa-manual-settings',
                className='mb-3',
            ),
            html.Div(
                [
                    dbc.Row(
                        [
                            labelled('Method', dcc.Dropdown(method_options, id='saa-method', clearable=False), 3),
                            labelled(
                                'Optimized every',
                                dcc.Dropdown(
                                    [{'label': v, 'value': k} for k, v in OPTIMIZATION_FREQ_LABELS.items()],
                                    id='saa-opt-freq',
                                    clearable=False,
                                ),
                                width=2,
                            ),
                        ]
                    ),
                    constraints,
                ],
                id='saa-optimize-settings',
                className='mb-3',
            ),
            # the background update of the prices (analytics.price_updates)
            html.Div(id='saa-price-status', className='small text-muted mb-2'),
            # the spinner only while all the cards are drawn: a grid being updated stays visible
            dcc.Loading(html.Div(id='saa-buckets'), target_components={'saa-buckets': 'children'}),
        ]
    )


def backtest_section() -> html.Div:
    """Step 4: run the backtest; the saved one against the benchmark and the portfolio."""
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Button(
                            ['Run backtest', html.I(className='fa-solid fa-gears ms-2')], id='saa-run', n_clicks=0
                        ),
                        width='auto',
                    ),
                    dbc.Col(loading_wrapper(html.Div(id='saa-run-status'))),
                ],
                align='center',
                className='mb-3',
            ),
            html.Div(id='saa-backtest-info', className='mb-2'),
            loading_wrapper(card_wrapper(dcc.Graph(id='saa-fig-navs', figure=get_fig_empty()))),
            html.Br(),
            # the metrics and the weights one above the other, side by side on wide screens (assets/saa.css)
            html.Div(
                [
                    card_wrapper(dcc.Graph(id='saa-fig-metrics', figure=get_fig_empty())),
                    card_wrapper(dcc.Graph(id='saa-fig-weights', figure=get_fig_empty())),
                ],
                className='saa-backtest-grid',
            ),
        ]
    )


def build_content_saa(account: Account) -> html.Div:
    """Layout only: the allocation of the account is loaded by the callbacks when the page is shown."""
    header = dbc.Row(
        [
            dbc.Col(
                dcc.Dropdown([a.name for a in list_accounts()], account.name, id='saa-account', clearable=False),
                width=2,
            ),
            dbc.Col(dcc.Dropdown(id='saa-select', clearable=False), width=3),
            dbc.Col(dbc.Input(id='saa-name', placeholder='Name of the allocation', debounce=True), width=2),
            # the main action stands out; the destructive one is marked red but less prominent
            dbc.Col(dbc.Button('Save', id='saa-save', n_clicks=0, color='success'), width='auto'),
            dbc.Col(dbc.Button('Delete', id='saa-delete', n_clicks=0, color='danger', outline=True), width='auto'),
            # the allocation as the benchmark of the account: manual weights, all of them set
            dbc.Col(
                dbc.Button(
                    'Set as benchmark', id='saa-benchmark', n_clicks=0, color='info', outline=True, disabled=True
                ),
                width='auto',
            ),
            dbc.Col(loading_wrapper(html.Div(id='saa-status'))),
        ],
        align='center',
        className='mb-3',
    )
    sections = {
        'classes': classes_section(),
        'splits': dcc.Loading(html.Div(id='saa-splits'), target_components={'saa-splits': 'children'}),
        'instruments': instruments_section(),
        'backtest': backtest_section(),
    }
    steps = html.Div(
        [
            dbc.Tabs(
                [dbc.Tab(label=v, tab_id=k, id=f'saa-tab-{k}') for k, v in STEPS.items()],
                id='saa-tabs',
                active_tab='classes',
            ),
            *[html.Div(s, id=f'saa-section-{k}', className='pt-3') for k, s in sections.items()],
        ]
    )
    # stays in view while the page scrolls (scrolls itself when taller than the window)
    summary = dbc.Card(
        dbc.CardBody([html.H6('Summary', className='mb-2'), html.Div(id='saa-summary')]),
        color='black',
        style={'position': 'sticky', 'top': '1rem', 'maxHeight': 'calc(100vh - 2rem)', 'overflowY': 'auto'},
    )
    return html.Div(
        dbc.Card(
            [
                dcc.Store(id='saa-allocation'),
                dcc.Store(id='saa-loaded'),  # changed when an allocation is loaded: redraws the steps
                dcc.Store(id='saa-backtest-version'),  # changed by a backtest: redraws its figures
                dcc.Store(id='saa-backtested'),  # the allocation as it was backtested (None: no backtest)
                dcc.Store(id='saa-buckets-drawn'),  # what the cards of step 3 were drawn for (see buckets_drawn)
                dcc.Store(id='saa-splits-drawn'),  # what step 2 was drawn for (see splits_drawn)
                dcc.ConfirmDialog(id='saa-delete-confirm'),  # asked before an allocation is deleted
                dcc.ConfirmDialog(id='saa-benchmark-confirm'),  # asked before the benchmark is replaced
                dcc.ConfirmDialog(id='saa-update-confirm'),  # asked before a backtest when new prices are available
                dcc.Store(id='saa-run-request'),  # a backtest to run without downloading prices first
                dcc.Interval(id='saa-price-status-interval', interval=10_000),  # refreshes the prices status
                html.H4('Strategic Asset Allocation', className='mb-3'),
                header,
                dbc.Row([dbc.Col(steps, width=9), dbc.Col(summary, width=3)]),
            ],
            body=True,
            color='dark',
        ),
        className='saa-page',  # page styles (assets/saa.css)
    )


def splits_content(account: Account, allocation: Allocation) -> list:
    """Step 2: for each class with a weight, whether it is divided into sub-classes and their weights."""
    blocks = []
    for asset_class in allocation.active_classes():
        options = SPLIT_OPTIONS.get(asset_class)
        if options is None:
            blocks.append(html.P(f'{asset_class}: no sub-classes', className='text-muted small'))
            continue
        dropdown = dcc.Dropdown(
            [{'label': 'Not divided', 'value': NO_SPLIT}] + [{'label': v, 'value': k} for k, v in options.items()],
            allocation.splits.get(asset_class, NO_SPLIT),
            id={'type': 'saa-split', 'index': asset_class},
            clearable=False,
        )
        blocks.append(
            dbc.Card(
                dbc.CardBody(
                    [
                        dbc.Row(
                            [
                                dbc.Col(html.H6(asset_class, className='mb-0'), width=3),
                                dbc.Col(dbc.Label('Divide by', className='mb-0 small text-end'), width=2),
                                dbc.Col(dropdown, width=3),
                            ],
                            align='center',
                        ),
                        html.Div(
                            bucket_inputs(account, allocation, asset_class),
                            id={'type': 'saa-parts', 'index': asset_class},
                        ),
                    ]
                ),
                color='black',
                className='mb-2',
            )
        )
    return blocks or [html.P('Give a weight to at least one asset class first.', className='text-muted')]


def parts_total(allocation: Allocation, asset_class: str) -> html.Span:
    """Total weight of the sub-classes of a class."""
    total = sum(b.weight for b in allocation.buckets if b.asset_class == asset_class)
    return total_text(total, f'Total {percent_text(total)} of {asset_class}')


def bucket_inputs(account: Account, allocation: Allocation, asset_class: str) -> list:
    """The sub-classes of a divided class (home region first, then by number of funds) with their weights in the
    class, and their total (updated while typing).
    """
    split = allocation.splits.get(asset_class)
    if split is None:
        return []
    weights = {b.value: b.weight for b in allocation.buckets if b.asset_class == asset_class}
    funds = split_values(account, asset_class, split)
    rows = [
        dbc.Row(
            [
                dbc.Col(dbc.Label(f'{value} ({n} funds)', className='mb-0 small'), width=4),
                dbc.Col(
                    pct_input(
                        {'type': 'saa-part', 'index': bucket_key(asset_class, value)},
                        pct(weights.get(value)),
                        live=True,
                    ),
                    width=3,
                ),
            ],
            className='mb-1',
            align='center',
        )
        for value, n in funds.items()
    ]
    rows.append(
        html.Div(
            parts_total(allocation, asset_class),
            id={'type': 'saa-parts-total', 'index': asset_class},
            className='small mt-1',
        )
    )
    return [html.Div(rows, className='mt-2')]


def bucket_cards(account: Account, allocation: Allocation, filters: list[str]) -> list:
    """Step 3: one card per bucket with its chosen instruments and its candidates."""
    cards = []
    for asset_class in allocation.active_classes():
        for bucket in allocation.class_buckets(asset_class):
            key = bucket_key(asset_class, bucket.value)
            title = asset_class if bucket.value is None else f'{asset_class} · {bucket.value}'
            rows = candidate_rows(
                account, allocation, asset_class, bucket.value, 'currency' in filters, 'leveraged' in filters
            )
            chosen = [i.ticker for i in allocation.bucket_instruments(asset_class, bucket.value)]
            grid = dag.AgGrid(
                id={'type': 'saa-grid', 'index': key},
                className='ag-theme-alpine-dark',
                columnDefs=CANDIDATE_COLUMNS,
                rowData=rows,
                getRowId='params.data.ticker',
                selectedRows={'ids': chosen},
                dashGridOptions={
                    'rowSelection': {'mode': 'multiRow', 'checkboxes': True, 'headerCheckbox': False},
                    'tooltipShowDelay': 300,
                },
                style={'height': '300px'},
            )
            search = dbc.Input(
                id={'type': 'saa-search', 'index': key},
                placeholder='Search by name, ISIN or ticker',
                debounce=True,
                size='sm',
            )
            cards.append(
                dbc.Card(
                    dbc.CardBody(
                        [
                            dbc.Row(
                                [dbc.Col(html.H6(title, className='mb-0'), width=5), dbc.Col(search, width=4)],
                                align='center',
                                className='mb-2',
                            ),
                            html.Div(
                                chosen_inputs(allocation, asset_class, bucket.value),
                                id={'type': 'saa-chosen', 'index': key},
                            ),
                            dcc.Loading(grid, overlay_style=LOADING_OVERLAY, delay_show=200),
                        ]
                    ),
                    color='black',
                    className='mb-2',
                )
            )
    return cards or [html.P('Give a weight to at least one asset class first.', className='text-muted')]


def chosen_total(allocation: Allocation, asset_class: str, bucket: str | None) -> html.Span:
    """Total manual weight of the chosen instruments of a bucket ('optimized' with optimized weights)."""
    if allocation.weight_mode == 'optimize':
        return html.Span('optimized', className='text-muted')
    total = sum(i.weight for i in allocation.bucket_instruments(asset_class, bucket))
    return total_text(total, f'total {percent_text(total)}')


def chosen_inputs(allocation: Allocation, asset_class: str, bucket: str | None) -> list:
    """The chosen instruments of a bucket with their weights in it (greyed out with optimized weights) and their
    total (updated while typing).
    """
    chosen = allocation.bucket_instruments(asset_class, bucket)
    if not chosen:
        return [html.P('Tick the instruments of this part below.', className='text-muted small mb-2')]
    optimized = allocation.weight_mode == 'optimize'
    items = [
        dbc.Col(
            [
                dbc.Label(i.ticker, className='small mb-1'),
                pct_input({'type': 'saa-weight', 'index': i.ticker}, pct(i.weight), disabled=optimized, live=True),
            ],
            width=2,
        )
        for i in chosen
    ]
    key = bucket_key(asset_class, bucket)
    items.append(
        dbc.Col(
            html.Div(
                chosen_total(allocation, asset_class, bucket),
                id={'type': 'saa-chosen-total', 'index': key},
                className='small',
            ),
            width=2,
        )
    )
    return [dbc.Row(items, align='end', className='mb-2')]


# FIGURES
def get_fig_saa_navs(backtest: SavedBacktest, name: str) -> go.Figure:
    """The allocation (under its name), the benchmark and the portfolio, each starting at 100."""
    fig = go.Figure(
        layout=go.Layout(
            template=LAYOUT_TEMPLATE,
            yaxis_title=dict(text='NAV'),
            legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1),
            margin=dict(t=40),  # no title: the legend only above the chart
        )
    )
    for label in backtest.navs.columns:
        series = backtest.navs[label].dropna()
        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=series.values,
                name=name if label == ALLOCATION_LABEL else label,
                mode='lines',
                hovertemplate='%{x|%Y/%m/%d}: %{y:.1f}<extra></extra>',
            )
        )
    return fig


def get_fig_saa_weights(backtest: SavedBacktest) -> go.Figure:
    """Weight of each instrument and of the cash at each month end; hovering shows all the weights of a month."""
    weights = backtest.weights.resample('M').last().dropna(how='all')
    weights = weights.loc[:, (weights.abs() > 1e-4).any()]
    fig = go.Figure(
        layout=go.Layout(
            template=LAYOUT_TEMPLATE,
            title=dict(text='Historical weights'),
            yaxis=dict(tickformat='.0%'),
            # one balloon per month wherever the mouse is (a stacked area only reacts on its edges otherwise)
            hovermode='x unified',
            xaxis=dict(hoverformat='%b %Y'),
            # the instruments in their order, cash last (plotly lists stacked traces in reverse by default)
            legend=dict(orientation='h', yanchor='top', y=-0.1, traceorder='normal'),
        )
    )
    for column in weights.columns:
        fig.add_trace(
            go.Scatter(
                x=weights.index,
                y=weights[column],
                name=str(column),
                stackgroup='weights',
                mode='lines',
                line=dict(width=0.5),
                hovertemplate=f'{column}: %{{y:.1%}}<extra></extra>',
            )
        )
    return fig


def metrics_label(backtest: SavedBacktest, label: str, name: str) -> str:
    """Column title of the metrics table: the allocation under its name, the others with their start when it is
    later than the allocation's.
    """
    if label == ALLOCATION_LABEL:
        return name
    start = backtest.navs[label].first_valid_index()
    return f'{label}<br>from {start:%Y-%m}' if start > backtest.navs.index[0] else label


# CALLBACKS
def _allocation(data: dict | None) -> Allocation:
    return Allocation.from_dict(data) if data else new_allocation()


def _ids(states: list[dict]) -> list[str]:
    return [s['id']['index'] for s in states]


def _options(names: list[str]) -> list[dict]:
    return [{'label': n, 'value': n} for n in names] + [{'label': '+ New allocation', 'value': NEW}]


@callback(
    Output('store-account', 'data', allow_duplicate=True),
    Input('saa-account', 'value'),
    prevent_initial_call=True,
)
def select_account_saa(account_name: str) -> str:
    """Remember the account selected on the page (shared with the other pages)."""
    return account_name


# load an allocation: the last saved one when the account changes, a new one on request
@callback(
    Output('saa-allocation', 'data'),
    Output('saa-loaded', 'data'),
    Output('saa-backtested', 'data'),
    Output('saa-select', 'options'),
    Output('saa-select', 'value'),
    Output('saa-name', 'value'),
    *[Output(i, 'value') for i in CLASS_INPUTS],
    *[Output(i, 'value') for i in SETTINGS_INPUTS],
    Input('saa-account', 'value'),
    Input('saa-select', 'value'),
    State('saa-allocation', 'data'),
)
def load_allocation(account_name: str, selected: str | None, current: dict | None) -> tuple:
    """Load the chosen allocation of the account (the last saved one when the account changes, or a new one) and fill
    the controls of the steps with it; its saved backtest counts as the backtest of the allocation as saved. Choosing
    the allocation already shown (e.g. after it was saved) loads nothing.
    """
    if ctx.triggered_id == 'saa-select' and current is not None and selected == current.get('name'):
        raise PreventUpdate
    names = list_allocations(account_name)
    if ctx.triggered_id != 'saa-select' or selected is None:
        selected = names[0] if names else NEW
    allocation = query_allocation(account_name, selected) if selected != NEW else None
    if allocation is None:
        allocation, selected = new_allocation(), NEW
    data = allocation.to_dict()
    account = get_account(account_name)
    backtested = data if has_saved_backtest(account, allocation.name) else None
    warm_up(account, allocation)  # steps 3 and 4 computed in the background, ready when they are shown
    settings = allocation.settings
    return (
        data,
        time.time(),
        backtested,
        _options(names),
        selected,
        allocation.name,
        *[pct(allocation.class_targets.get(c)) for c in ASSET_CLASSES],
        allocation.weight_mode,
        allocation.rebalancing_freq,
        settings.method.value,
        settings.optimization_freq,
        pct(settings.class_tolerance),
        None if settings.max_asset_exposure >= 1.0 else pct(settings.max_asset_exposure),
        pct(settings.max_vol),
        pct(settings.target_vol),
    )


# show the section of the chosen step
@callback(*[Output(f'saa-section-{k}', 'style') for k in STEPS], Input('saa-tabs', 'active_tab'))
def show_step(active_tab: str) -> tuple:
    """Show the section of the active step only."""
    return tuple({} if k == active_tab else {'display': 'none'} for k in STEPS)


# a check mark on the tab of each complete step
@callback(
    *[Output(f'saa-tab-{k}', 'label_class_name') for k in STEPS],
    Input('saa-allocation', 'data'),
    Input('saa-backtested', 'data'),
)
def mark_completed_steps(data: dict | None, backtested: dict | None) -> tuple:
    """Check marks on the tabs of the complete steps; the backtest one when the allocation as it stands was
    backtested.
    """
    if data is None:
        raise PreventUpdate
    done = completed_steps(_allocation(data))
    done['backtest'] = backtested is not None and backtested == data
    return tuple(DONE_CLASS if done[k] else '' for k in STEPS)


@callback(
    Output('saa-allocation', 'data', allow_duplicate=True),
    Input('saa-name', 'value'),
    State('saa-allocation', 'data'),
    prevent_initial_call=True,
)
def edit_name(name: str | None, data: dict) -> dict:
    """Name of the allocation."""
    allocation = _allocation(data)
    allocation.name = (name or '').strip()
    return allocation.to_dict()


@callback(
    *[Output(i, 'value', allow_duplicate=True) for i in CLASS_INPUTS],
    Input('saa-preset', 'value'),
    prevent_initial_call=True,
)
def apply_preset(preset: str | None) -> tuple:
    """Class weights of a preset."""
    if preset is None:
        raise PreventUpdate
    return tuple(pct(PRESETS[preset].get(c)) for c in ASSET_CLASSES)


@callback(
    Output('saa-allocation', 'data', allow_duplicate=True),
    Output('saa-class-total', 'children'),
    Input({'type': 'saa-class', 'index': ALL}, 'value'),
    State('saa-allocation', 'data'),
    prevent_initial_call=True,
)
def edit_classes(values: list, data: dict) -> tuple:
    """Class weights, and their total (while typing)."""
    targets = {c: fraction(v) for c, v in zip(_ids(ctx.inputs_list[0]), values, strict=True)}
    allocation = set_class_targets(_allocation(data), targets)
    total = sum(targets.values())
    return allocation.to_dict(), total_text(total, f'Total {percent_text(total)}')


def splits_drawn(account_name: str, loaded, allocation: Allocation) -> dict:
    """What step 2 shows: the account, the allocation loaded, its classes with a weight and how they are divided (the
    weights of the sub-classes are updated in place).
    """
    classes = allocation.active_classes()
    return {'account': account_name, 'loaded': loaded, 'splits': {c: allocation.splits.get(c) for c in classes}}


@callback(
    Output('saa-splits', 'children'),
    Output('saa-splits-drawn', 'data'),
    Input('saa-tabs', 'active_tab'),
    Input('saa-loaded', 'data'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    State('saa-splits-drawn', 'data'),
)
def draw_splits(active_tab: str, loaded, data: dict | None, account_name: str, drawn: dict | None) -> tuple:
    """Step 2: drawn as soon as an allocation is loaded (it is quick, so it shows at once), and again when it is
    shown after a change of the classes in step 1; otherwise kept as it is.
    """
    if data is None or (ctx.triggered_id == 'saa-tabs' and active_tab != 'splits'):
        raise PreventUpdate
    allocation = _allocation(data)
    needed = splits_drawn(account_name, loaded, allocation)
    if needed == drawn:
        raise PreventUpdate
    return splits_content(get_account(account_name), allocation), needed


@callback(
    Output('saa-allocation', 'data', allow_duplicate=True),
    Output({'type': 'saa-parts', 'index': ALL}, 'children'),
    Output('saa-splits-drawn', 'data', allow_duplicate=True),
    Input({'type': 'saa-split', 'index': ALL}, 'value'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    State('saa-loaded', 'data'),
    prevent_initial_call=True,
)
def edit_splits(values: list, data: dict, account_name: str, loaded) -> tuple:
    """How the classes are divided; a new division shows its sub-classes, without weights (in place: the step is
    kept as it is when shown again).
    """
    allocation = _allocation(data)
    classes = _ids(ctx.inputs_list[0])
    for asset_class, split in zip(classes, values, strict=True):
        allocation = set_split(allocation, asset_class, None if split == NO_SPLIT else split)
    account = get_account(account_name)
    parts = [bucket_inputs(account, allocation, c) for c in classes]
    return allocation.to_dict(), parts, splits_drawn(account_name, loaded, allocation)


@callback(
    Output('saa-allocation', 'data', allow_duplicate=True),
    Output({'type': 'saa-parts-total', 'index': ALL}, 'children'),
    Input({'type': 'saa-part', 'index': ALL}, 'value'),
    State('saa-allocation', 'data'),
    prevent_initial_call=True,
)
def edit_parts(values: list, data: dict) -> tuple:
    """Weights of the sub-classes, and their total per class (while typing)."""
    allocation = _allocation(data)
    by_class: dict[str, dict[str, float]] = {}
    for key, value in zip(_ids(ctx.inputs_list[0]), values, strict=True):
        asset_class, part = parse_bucket_key(key)
        by_class.setdefault(asset_class, {})[part] = fraction(value)
    for asset_class, weights in by_class.items():
        allocation = set_bucket_weights(allocation, asset_class, weights)
    totals = [parts_total(allocation, c) for c in _ids(ctx.outputs_list[1])]
    return allocation.to_dict(), totals


def buckets_drawn(account_name: str, loaded, allocation: Allocation) -> dict:
    """What the cards of step 3 show: the account, the allocation loaded, its buckets and the instruments chosen for
    each (the other changes made in step 3 update the cards in place).
    """
    buckets = {
        bucket_key(c, b.value): [i.ticker for i in allocation.bucket_instruments(c, b.value)]
        for c in allocation.active_classes()
        for b in allocation.class_buckets(c)
    }
    return {'account': account_name, 'loaded': loaded, 'buckets': buckets}


@callback(
    Output('saa-buckets', 'children'),
    Output('saa-buckets-drawn', 'data'),
    Input('saa-tabs', 'active_tab'),
    Input('saa-loaded', 'data'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    State('saa-filters', 'value'),
    State('saa-buckets-drawn', 'data'),
)
def draw_buckets(
    active_tab: str, loaded, data: dict | None, account_name: str, filters: list[str], drawn: dict | None
) -> tuple:
    """Step 3, drawn when it is shown, unless its cards were drawn for the same buckets (they are kept)."""
    if active_tab != 'instruments' or data is None:
        raise PreventUpdate
    allocation = _allocation(data)
    needed = buckets_drawn(account_name, loaded, allocation)
    if needed == drawn:
        raise PreventUpdate
    return bucket_cards(get_account(account_name), allocation, filters or []), needed


def _bucket_candidates(account_name: str, data: dict, key: str, filters: list[str] | None, search) -> tuple:
    """Candidates of a bucket and its selection (the chosen instruments)."""
    account, allocation = get_account(account_name), _allocation(data)
    filters = filters or []
    asset_class, bucket = parse_bucket_key(key)
    rows = candidate_rows(
        account, allocation, asset_class, bucket, 'currency' in filters, 'leveraged' in filters, search
    )
    return rows, {'ids': [i.ticker for i in allocation.bucket_instruments(asset_class, bucket)]}


@callback(
    Output({'type': 'saa-grid', 'index': ALL}, 'rowData'),
    Output({'type': 'saa-grid', 'index': ALL}, 'selectedRows'),
    Input('saa-filters', 'value'),
    State({'type': 'saa-search', 'index': ALL}, 'value'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def filter_candidates(filters: list[str], searches: list, data: dict, account_name: str) -> tuple:
    """Candidates of every bucket after a change of the filters (the chosen ones stay)."""
    results = [
        _bucket_candidates(account_name, data, key, filters, search)
        for key, search in zip(_ids(ctx.states_list[0]), searches, strict=True)
    ]
    return [r for r, _ in results], [s for _, s in results]


@callback(
    Output({'type': 'saa-grid', 'index': MATCH}, 'rowData', allow_duplicate=True),
    Output({'type': 'saa-grid', 'index': MATCH}, 'selectedRows', allow_duplicate=True),
    Input({'type': 'saa-search', 'index': MATCH}, 'value'),
    State('saa-filters', 'value'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def search_candidates(search: str | None, filters: list[str], data: dict, account_name: str) -> tuple:
    """Candidates of one bucket matching its search (the chosen ones stay); the other buckets are left as they are."""
    return _bucket_candidates(account_name, data, ctx.inputs_list[0]['id']['index'], filters, search)


def _selected_tickers(selection) -> list[tuple[str, str | None]]:
    """(ticker, ISIN) of the selected rows of a grid (rows, or {'ids': [...]} before the grid reports its rows)."""
    if not selection:
        return []
    if isinstance(selection, dict):
        return [(t, None) for t in selection.get('ids', [])]
    return [(row['ticker'], row.get('isin')) for row in selection]


@callback(
    Output('saa-allocation', 'data', allow_duplicate=True),
    Output({'type': 'saa-chosen', 'index': ALL}, 'children'),
    Output('saa-buckets-drawn', 'data', allow_duplicate=True),
    Input({'type': 'saa-grid', 'index': ALL}, 'selectedRows'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    State('saa-loaded', 'data'),
    prevent_initial_call=True,
)
def choose_instruments(selections: list, data: dict, account_name: str, loaded) -> tuple:
    """Instruments ticked in the candidates of each bucket (a changed choice starts from equal weights); the cards
    show them, so they are kept when the step is shown again.
    """
    allocation = _allocation(data)
    keys = _ids(ctx.inputs_list[0])
    for key, selection in zip(keys, selections, strict=True):
        asset_class, bucket = parse_bucket_key(key)
        known = {i.ticker: i.isin for i in allocation.instruments}
        chosen = [(t, isin or known.get(t)) for t, isin in _selected_tickers(selection)]
        allocation = set_bucket_selection(allocation, asset_class, bucket, chosen)
    chosen = [chosen_inputs(allocation, *parse_bucket_key(k)) for k in keys]
    return allocation.to_dict(), chosen, buckets_drawn(account_name, loaded, allocation)


@callback(
    Output('saa-allocation', 'data', allow_duplicate=True),
    Output({'type': 'saa-chosen-total', 'index': ALL}, 'children'),
    Input({'type': 'saa-weight', 'index': ALL}, 'value'),
    State('saa-allocation', 'data'),
    prevent_initial_call=True,
)
def edit_weights(values: list, data: dict) -> tuple:
    """Manual weights of the chosen instruments in their bucket, and their total per bucket (while typing)."""
    weights = {t: fraction(v) for t, v in zip(_ids(ctx.inputs_list[0]), values, strict=True)}
    allocation = set_instrument_weights(_allocation(data), weights)
    totals = [chosen_total(allocation, *parse_bucket_key(k)) for k in _ids(ctx.outputs_list[1])]
    return allocation.to_dict(), totals


@callback(
    Output('saa-allocation', 'data', allow_duplicate=True),
    Output('saa-manual-settings', 'style'),
    Output('saa-optimize-settings', 'style'),
    Output({'type': 'saa-weight', 'index': ALL}, 'disabled'),
    Output({'type': 'saa-chosen-total', 'index': ALL}, 'children', allow_duplicate=True),
    Output('saa-max-vol', 'disabled'),
    Output('saa-target-vol', 'disabled'),
    *[Input(i, 'value') for i in SETTINGS_INPUTS],
    State('saa-allocation', 'data'),
    prevent_initial_call=True,
)
def edit_settings(
    mode, rebalancing, method, opt_freq, tolerance, max_weight, max_vol, target_vol, data: dict | None
) -> tuple:
    """Weight mode, rebalancing and optimization settings; only the settings of the mode are shown, and the
    volatility constraints only apply to their method.
    """
    if data is None or mode is None:
        raise PreventUpdate

    def optional(value) -> float | None:
        return None if value in (None, '') or fraction(value) <= 0 else fraction(value)

    allocation = _allocation(data)
    allocation.weight_mode = mode
    allocation.rebalancing_freq = rebalancing or allocation.rebalancing_freq
    settings = allocation.settings
    settings.method = AllocationStrats(method) if method else settings.method
    settings.optimization_freq = opt_freq or settings.optimization_freq
    settings.class_tolerance = fraction(tolerance)
    settings.max_asset_exposure = optional(max_weight) or 1.0
    settings.max_vol = optional(max_vol)
    settings.target_vol = optional(target_vol)
    optimized = mode == 'optimize'
    hidden = {'display': 'none'}
    totals = [chosen_total(allocation, *parse_bucket_key(k)) for k in _ids(ctx.outputs_list[4])]
    return (
        allocation.to_dict(),
        hidden if optimized else {},
        {} if optimized else hidden,
        [optimized for _ in ctx.outputs_list[3]],
        totals,
        settings.method != AllocationStrats.MAX_SHARPE,
        settings.method != AllocationStrats.MAX_RET,
    )


@callback(Output('saa-summary', 'children'), Input('saa-allocation', 'data'))
def draw_summary(data: dict | None) -> list:
    """The allocation as it stands, and what keeps it from being backtested."""
    if data is None:
        raise PreventUpdate
    allocation = _allocation(data)
    rows = [
        html.Div(
            [html.Span(label), html.Span(weight, className='float-end')],
            style={'paddingLeft': f'{level}rem'},
            className='small' + (' fw-bold' if level == 0 else ''),
        )
        for level, label, weight in summary_rows(allocation)
    ]
    mode = 'Manual weights' if allocation.weight_mode == 'manual' else 'Optimized weights'
    detail = (
        f'rebalanced {REBALANCING_FREQ_LABELS[allocation.rebalancing_freq].lower()}'
        if allocation.weight_mode == 'manual'
        else f'{ALLOCATION_STRATS_LABELS[allocation.settings.method]}, '
        f'{OPTIMIZATION_FREQ_LABELS[allocation.settings.optimization_freq].lower()}, '
        f'classes ±{percent_text(allocation.settings.class_tolerance)}'
    )
    problems = allocation.problems()
    status = (
        [html.Div(p, className='text-warning small') for p in problems]
        if problems
        else [html.Div('Ready to backtest', className='text-success small')]
    )
    return [*rows, html.Hr(className='my-2'), html.Div(f'{mode}: {detail}', className='small mb-2'), *status]


@callback(
    Output('saa-status', 'children'),
    Output('saa-select', 'options', allow_duplicate=True),
    Output('saa-select', 'value', allow_duplicate=True),
    Input('saa-save', 'n_clicks'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def save(n_clicks, data: dict, account_name: str) -> tuple:
    """Save the allocation under its name (replacing the one with the same name)."""
    if not n_clicks:
        raise PreventUpdate
    allocation = _allocation(data)
    try:
        save_allocation(account_name, allocation)
    except ValueError as e:
        return html.Span(f'Not saved: {e}', className='text-danger'), no_update, no_update
    return f'Saved {time.strftime("%H:%M")}', _options(list_allocations(account_name)), allocation.name


@callback(
    Output('saa-delete-confirm', 'displayed'),
    Output('saa-delete-confirm', 'message'),
    Output('saa-status', 'children', allow_duplicate=True),
    Input('saa-delete', 'n_clicks'),
    State('saa-select', 'value'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def ask_delete(n_clicks, selected: str | None, account_name: str) -> tuple:
    """Ask for a confirmation before deleting the saved allocation shown and its saved backtest (nothing to delete for
    a new one).
    """
    if not n_clicks:
        raise PreventUpdate
    if selected in (None, NEW):
        return False, no_update, html.Span('Nothing to delete: this allocation is not saved', className='text-warning')
    backtest = ' and its saved backtest' if has_saved_backtest(get_account(account_name), selected) else ''
    return True, f'Delete the allocation "{selected}" of {account_name}{backtest}?\n\nThis cannot be undone.', None


@callback(
    Output('saa-status', 'children', allow_duplicate=True),
    Output('saa-select', 'value', allow_duplicate=True),
    Input('saa-delete-confirm', 'submit_n_clicks'),
    State('saa-select', 'value'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def delete(submitted, selected: str | None, account_name: str) -> tuple:
    """Delete the saved allocation shown and its saved backtest once confirmed (then a new one is shown)."""
    if not submitted or selected in (None, NEW):
        raise PreventUpdate
    delete_allocation(account_name, selected)
    removed = delete_saved_backtest(get_account(account_name), selected)
    return f'Deleted {selected}' + (' and its backtest' if removed else ''), NEW


@callback(Output('saa-benchmark', 'disabled'), Input('saa-allocation', 'data'))
def enable_benchmark(data: dict | None) -> bool:
    """Set as benchmark only with manual weights, all of them set."""
    return data is None or not can_be_benchmark(_allocation(data))


@callback(
    Output('saa-benchmark-confirm', 'displayed'),
    Output('saa-benchmark-confirm', 'message'),
    Input('saa-benchmark', 'n_clicks'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def ask_benchmark(n_clicks, data: dict, account_name: str) -> tuple:
    """Ask for a confirmation before replacing the benchmark of the account, showing the current one and the new one."""
    if not n_clicks:
        raise PreventUpdate
    allocation = _allocation(data)
    try:
        current = account_benchmark(get_account(account_name)).describe()
    except ValueError:
        current = 'none'
    new = benchmark_from_allocation(allocation).describe()
    name = f' "{allocation.name}"' if allocation.name else ''
    message = (
        f'Use the allocation{name} as the benchmark of {account_name}?\n\n'
        f'Current benchmark: {current}\n\nNew benchmark: {new}\n\n'
        'The benchmark backtest is computed again; the Portfolio page and the comparisons use the new benchmark.'
    )
    return True, message


@callback(
    Output('saa-status', 'children', allow_duplicate=True),
    Output('saa-backtest-version', 'data', allow_duplicate=True),
    Input('saa-benchmark-confirm', 'submit_n_clicks'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def set_benchmark(submitted, data: dict, account_name: str) -> tuple:
    """Save the allocation as the benchmark of the account once confirmed, and compute the benchmark backtest again
    (the backtest tab is redrawn against it).
    """
    allocation = _allocation(data)
    if not submitted or not can_be_benchmark(allocation):
        raise PreventUpdate
    account = get_account(account_name)
    try:
        save_benchmark(account_name, benchmark_from_allocation(allocation))
    except ValueError as e:
        return html.Span(f'Benchmark not set: {e}', className='text-danger'), no_update
    try:
        refresh_benchmark(account)
    except FileNotFoundError:  # the account has no saved backtest yet: computed at its first Update
        return f'Benchmark set {time.strftime("%H:%M")} (computed at the next Update)', time.time()
    except Exception as e:  # e.g. no connection to Yahoo Finance: the new benchmark is saved anyway
        logger.exception('Backtest of the new benchmark of %s failed', account_name)
        return html.Span(f'Benchmark set, but not computed: {e}', className='text-warning'), time.time()
    return f'Benchmark set {time.strftime("%H:%M")}', time.time()


@callback(Output('saa-price-status', 'children'), Input('saa-price-status-interval', 'n_intervals'))
def show_price_status(n_intervals) -> str:
    """State of the background update of the prices, refreshed every few seconds."""
    return status_text(UPDATER.status)


def backtest_tickers(account: Account, allocation: Allocation) -> list[str]:
    """The listings a backtest of the allocation uses: its instruments, and the benchmark's it is compared with."""
    try:
        benchmark = [c.ticker for c in account_benchmark(account).components if c.ticker]
    except ValueError:  # no benchmark
        benchmark = []
    return list(dict.fromkeys([i.ticker for i in allocation.instruments] + benchmark))


@callback(
    Output('saa-update-confirm', 'displayed'),
    Output('saa-update-confirm', 'message'),
    Output('saa-run-request', 'data'),
    Input('saa-run', 'n_clicks'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def prepare_backtest(n_clicks, data: dict, account_name: str) -> tuple:
    """Before a backtest: if prices of a later final trading day should be available for its listings, ask whether
    to download them first; otherwise run it at once.
    """
    if not n_clicks:
        raise PreventUpdate
    tickers = backtest_tickers(get_account(account_name), _allocation(data))
    due = due_listings(tickers)
    if not due:
        return False, no_update, time.time()
    lines = '\n'.join(f'  {t}: last stored {d:%a %d %b}' if d else f'  {t}: no prices stored' for t, d in due.items())
    message = (
        f'New prices should be available for {len(due)} of the {len(tickers)} instruments of the backtest '
        f'(latest final trading day: {latest_final_day():%a %d %b}):\n{lines}\n\n'
        'Download them before the backtest?\n(Cancel: backtest with the prices stored now.)'
    )
    return True, message, no_update


@callback(
    Output('saa-backtest-version', 'data'),
    Output('saa-backtested', 'data', allow_duplicate=True),
    Output('saa-run-status', 'children'),
    Input('saa-run-request', 'data'),
    Input('saa-update-confirm', 'submit_n_clicks'),
    Input('saa-update-confirm', 'cancel_n_clicks'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
    prevent_initial_call=True,
)
def run_backtest(request, submitted, cancelled, data: dict, account_name: str) -> tuple:
    """Backtest the allocation as it stands (saved under its name), after downloading the new prices of its listings
    if asked; the new version redraws the figures.
    """
    if ctx.triggered_id is None or not ctx.triggered[0]['value']:
        raise PreventUpdate
    account, allocation = get_account(account_name), _allocation(data)
    note = ''
    if ctx.triggered_id == 'saa-update-confirm' and ctx.triggered[0]['prop_id'].endswith('submit_n_clicks'):
        due = list(due_listings(backtest_tickers(account, allocation)))
        update_listings(due)
        still = list(due_listings(due))
        note = f', prices of {len(due) - len(still)} of {len(due)} instruments updated'
        if still:
            note += f' (none yet for {", ".join(still)})'
    try:
        result = backtest_allocation(account, allocation)
    except ValueError as e:
        return no_update, no_update, html.Span(f'Not run: {e}{note}', className='text-danger')
    saved_backtest(account, allocation.name)  # its comparison with the benchmark and the portfolio, saved with it
    failed = f', {len(result.failed_dates)} optimizations failed' if result.failed_dates else ''
    return time.time(), data, f'Backtested {time.strftime("%H:%M")}{note}{failed}'


@callback(
    Output('saa-fig-navs', 'figure'),
    Output('saa-fig-metrics', 'figure'),
    Output('saa-fig-weights', 'figure'),
    Output('saa-backtest-info', 'children'),
    Input('saa-tabs', 'active_tab'),
    Input('saa-backtest-version', 'data'),
    Input('saa-loaded', 'data'),
    State('saa-allocation', 'data'),
    State('saa-account', 'value'),
)
def draw_backtest(active_tab: str, version, loaded, data: dict | None, account_name: str) -> tuple:
    """Step 4 from the saved backtest of the allocation, drawn when it is shown."""
    if active_tab != 'backtest' or data is None:
        raise PreventUpdate
    allocation = _allocation(data)
    backtest = saved_backtest(get_account(account_name), allocation.name) if allocation.name else None
    if backtest is None:
        message = dbc.Alert('No backtest of this allocation yet: press "Run backtest".', color='info')
        return get_fig_empty(), get_fig_empty(), get_fig_empty(), message
    computed = f'{datetime.fromtimestamp(backtest.saved_at):%Y-%m-%d %H:%M}' if backtest.saved_at else 'unknown'
    info = html.Div(
        f'Backtest computed on {computed}, from {backtest.navs.index[0]:%Y-%m-%d} to {backtest.navs.index[-1]:%Y-%m-%d}'
        f' (in the account currency, 0.10% transaction costs). {start_text(backtest)}'
        ' Run it again after changing the allocation.',
        className='small text-muted',
    )
    labels = {k: metrics_label(backtest, k, allocation.name) for k in backtest.metrics}
    metrics = get_fig_metrics_table({labels[k]: v for k, v in backtest.metrics.items()})
    figs = (get_fig_saa_navs(backtest, allocation.name), metrics, get_fig_saa_weights(backtest))
    return *(f.update_layout(uirevision=f'{account_name} {allocation.name}') for f in figs), info
