"""Markets page: monthly returns of sectors, world indices, bonds, commodities, currencies and crypto."""

import math

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html
from plotly.subplots import make_subplots

from portfolio_manager.analytics.markets import market_returns, refresh_market_data
from portfolio_manager.config.markets import MARKET_INSTRUMENTS
from portfolio_manager.dashboard.common import LAYOUT_TEMPLATE, loading_wrapper

# red for losses, white at 0%, green for gains
COLOR_SCALE = [[0.0, 'rgb(215, 25, 28)'], [0.5, 'rgb(255, 255, 255)'], [1.0, 'rgb(26, 150, 65)']]
ROW_HEIGHT = 24  # pixels
TOTAL_COLUMN = 'Last 12 months'


def scale_limit(values: np.ndarray) -> float:
    """Largest absolute value (the end of a scale symmetric around 0); a tiny value if there is none."""
    finite = np.abs(values[np.isfinite(values)])
    return float(finite.max()) if finite.size and finite.max() > 0 else 1e-9


def percent(value: float) -> str:
    """A return as text, e.g. +2.3% (empty if unknown)."""
    return '' if value is None or not math.isfinite(value) else f'{value:+.1%}'


def get_fig_market_returns(returns: pd.DataFrame, totals: pd.Series) -> go.Figure:
    """Two tables side by side, scrolling together: the monthly returns (ticker x month) and their total, each with its
    own color scale (red, white at 0%, green), symmetric around 0 up to its largest absolute return; the instruments
    in groups, each under a title row.
    """
    labels, monthly, total = [], [], []
    columns = list(returns.columns)
    for group in dict.fromkeys(i.group for i in MARKET_INSTRUMENTS):
        labels.append(f'<b>{group}</b>')  # a title row, without values
        monthly.append([np.nan] * len(columns))
        total.append([np.nan])
        for instrument in (i for i in MARKET_INSTRUMENTS if i.group == group):
            labels.append(f'{instrument.label} ({instrument.ticker})')
            row = returns.loc[instrument.ticker] if instrument.ticker in returns.index else pd.Series(dtype=float)
            monthly.append([row.get(c, np.nan) for c in columns])
            total.append([totals.get(instrument.ticker, np.nan)])
    monthly, total = np.array(monthly, dtype=float), np.array(total, dtype=float)
    month_labels = [p.strftime('%b %y') for p in columns]

    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, column_widths=[0.86, 0.14], horizontal_spacing=0.08)
    for col, values, x, colorbar_x in [(1, monthly, month_labels, None), (2, total, [TOTAL_COLUMN], 1.0)]:
        limit = scale_limit(values)
        domain = fig.layout[f'xaxis{"" if col == 1 else col}'].domain
        fig.add_trace(
            go.Heatmap(
                z=values,
                x=x,
                y=labels,
                text=[[percent(v) for v in row] for row in values],
                texttemplate='%{text}',
                textfont={'color': 'black', 'size': 11},
                hovertemplate='%{y}<br>%{x}: %{text}<extra></extra>',
                colorscale=COLOR_SCALE,
                zmin=-limit,
                zmax=limit,
                zmid=0.0,
                xgap=1,
                ygap=1,
                colorbar={
                    'x': colorbar_x if colorbar_x is not None else domain[1] + 0.005,
                    'tickformat': '+.0%',
                    'thickness': 12,
                    'len': 0.6,
                },
            ),
            row=1,
            col=col,
        )
    fig.update_yaxes(autorange='reversed', showgrid=False, ticklabelstandoff=10)  # space before the table
    fig.update_xaxes(side='top', showgrid=False)
    fig.update_layout(
        template=LAYOUT_TEMPLATE,
        height=ROW_HEIGHT * len(labels) + 120,
        margin={'l': 10, 'r': 40, 't': 60, 'b': 10},
    )
    return fig


def build_fig_market_returns() -> go.Figure:
    """The tables of the stored market data."""
    returns, totals, _ = market_returns()
    return get_fig_market_returns(returns, totals)


def last_close_text() -> str:
    """When the stored market data ends."""
    _, _, last_day = market_returns()
    return f'Last close: {last_day:%d %b %Y}' if last_day is not None else 'No market data yet: press Update'


# DASHBOARD
def build_content_markets() -> html.Div:
    """Layout of the Markets page (the stored data; Update downloads the new days)."""
    return html.Div(
        dbc.Card(
            dbc.CardBody(
                [
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    html.H4('Market returns'),
                                    html.Div(
                                        'Last 12 monthly returns, up to date for the current month, and their total; '
                                        'each instrument in its own currency, total returns.',
                                        className='text-muted',
                                    ),
                                ],
                                width=9,
                            ),
                            dbc.Col(
                                [
                                    dbc.Button('Update market data', id='button-update-markets', color='primary'),
                                    html.Div(last_close_text(), id='text-markets-status', className='text-muted mt-1'),
                                ],
                                width=3,
                                className='text-end',
                            ),
                        ],
                        className='mb-3',
                    ),
                    loading_wrapper(dcc.Graph(id='fig-market-returns', figure=build_fig_market_returns())),
                ]
            ),
            color='dark',
        )
    )


@callback(
    Output('fig-market-returns', 'figure'),
    Output('text-markets-status', 'children'),
    Input('button-update-markets', 'n_clicks'),
    prevent_initial_call=True,
)
def update_markets(n_clicks) -> tuple[go.Figure, str]:
    """Download the new days of the market data, then redraw the tables."""
    results = refresh_market_data()
    failed = f' ({results["failed"]} failed)' if results.get('failed') else ''
    return build_fig_market_returns(), last_close_text() + failed
