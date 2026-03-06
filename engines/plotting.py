from enum import Enum
from typing import NamedTuple, Union, Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from config.definitions import FIGURES_DIR
from reporting import lin_reg, poly_reg

DATE_FORMAT = mdates.DateFormatter('%b%y')
DEFAULT_PLOT_FONT_SIZE = 14

FIG_DPI = 300

PANDAS_DATA_TYPE = Union[pd.DataFrame, pd.Series]


class CustomColor(NamedTuple):
    red: int
    green: int
    blue: int

    def to_scaled(self):
        return self.red / 255, self.green / 255, self.blue / 255


class CustomColors(CustomColor, Enum):
    RED = CustomColor(240, 78, 104)
    PINK = CustomColor(246, 178, 182)
    LIGHT_PINK = CustomColor(252, 233, 235)
    DARK_GREY = CustomColor(83, 80, 96)
    GREY = CustomColor(121, 119, 133)
    LIGHT_GREY = CustomColor(207, 205, 218)
    LIGHT_BLUE = CustomColor(217, 229, 236)
    RED_2 = CustomColor(104, 78, 240)
    PINK_2 = CustomColor(182, 178, 246)
    LIGHT_PINK_2 = CustomColor(235, 233, 252)


def plot_standard_time_series(df: pd.DataFrame, 
                              fig_name: str, 
                              title: str
                              ) -> plt.Figure:
    fig = plt.figure()
    for (col, color) in zip(df.columns.to_list(), [color for color in CustomColors]):
        plt.plot(df[col].index, df[col].values, c=color.to_scaled(), label=col)
    plt.legend()
    plt.grid(axis='y')
    plt.ylabel(title)
    plt.gca().xaxis.set_major_formatter(DATE_FORMAT)
    for pos in ['right', 'top', 'bottom', 'left']:
        plt.gca().spines[pos].set_visible(False)
    plt.tick_params(left=False, bottom=False)
    fig.savefig(f'{FIGURES_DIR}/{fig_name}.png', bbox_inches='tight', dpi=FIG_DPI, transparent=True)
    return fig


def plot_navs(navs: PANDAS_DATA_TYPE,
              fig_name: str,
              dropna: bool = True,
              initial_value: float = 100.,
              nav_forecasts: Optional[pd.DataFrame] = None
              ) -> plt.Figure:
    if isinstance(navs, pd.Series):
        navs = navs.to_frame()
    if dropna:
        navs = navs.dropna()
    navs = navs.div(navs.iloc[0, :]) * initial_value
    if nav_forecasts is not None:
        fig = plot_standard_time_series(df=navs, fig_name=fig_name, title='NAV Forecast')
        fig = plot_forecast_nav_overlay(df=nav_forecasts, fig=fig, fig_name=fig_name)
    else:
        fig = plot_standard_time_series(df=navs, fig_name=fig_name, title='NAV')
    return fig


def plot_weights(weights: PANDAS_DATA_TYPE,
                 fig_name: str
                 ) -> plt.Figure:
    return plot_standard_time_series(weights, fig_name, 'Weights')


def plot_returns_strat_vs_bm(returns_df: pd.DataFrame,
                             benchmark: Union[StrategyConfiguration, str],
                             show_regression_lines: bool = True):
    if isinstance(benchmark, StrategyConfiguration):
        benchmark = benchmark.name
    returns_bm = returns_df[benchmark].copy()
    for col in returns_df.columns.to_list():
        fig = plt.figure()
        returns_strat = returns_df[col].copy()
        plt.scatter(100. * returns_bm, 100. * returns_strat, s=12)
        if show_regression_lines:
            x_pts = np.ndarray(np.linspace(returns_bm.min(), returns_bm.max(), 101))
            coeffs, pvalues, r2 = lin_reg(y=returns_strat, x=returns_bm)
            alpha, beta, pval_alpha, pval_beta = coeffs[0], coeffs[1], pvalues[0], pvalues[1]
            plt.plot(100. * x_pts, 100. * (alpha + beta * x_pts), c='red', linestyle='dashed', linewidth=1, alpha=0.5)
            coeffs, pvalues, r2 = poly_reg(y=returns_strat, x=returns_bm, degree=2)
            alpha, beta, gamma, pval_alpha, pval_beta, pval_gamma = coeffs[0], coeffs[1], coeffs[2], pvalues[0], pvalues[1], pvalues[2]
            plt.plot(100. * x_pts, 100. * (alpha + beta * x_pts + gamma * x_pts ** 2), c='red', linewidth=1, alpha=0.5)
            textstr = '\n'.join((fr'$\alpha={alpha:.2f}~~({pval_alpha:.2f})$',
                                 fr'$\beta={beta:.2f}~~({pval_beta:.2f})$',
                                 fr'$\gamma={gamma:.2f}~~({pval_gamma:.2f})$',
                                 fr'$R^2={r2:.2f}$'))
            # these are matplotlib.patch.Patch properties
            props = dict(boxstyle='round', facecolor='wheat', alpha=0.5)
            # place a text box in upper left in axes coords
            fig.text(0.05, 0.95, textstr, transform=plt.gca().transAxes, fontsize=DEFAULT_PLOT_FONT_SIZE,
                     horizontalalignment='left', verticalalignment='top', bbox=props)
        plt.xlabel(f'{returns_bm.name} returns [%]')
        plt.ylabel(f'{returns_strat.name} returns [%]')
        fig.savefig(f'{FIGURES_DIR}/returns_{returns_strat.name}_vs_{returns_bm.name}.png',
                    bbox_inches='tight', dpi=FIG_DPI, transparent=True)


def render_mpl_table(data,
                     col_width=3.0,
                     row_height=0.625,
                     font_size=10,
                     header_color=CustomColors.RED.to_scaled(),
                     row_colors=(CustomColors.PINK.to_scaled(), CustomColors.LIGHT_PINK.to_scaled()),
                     edge_color='w',
                     bbox=(0, 0, 1, 1),
                     header_columns=0,
                     ax=None,
                     **kwargs):
    if ax is None:
        size = (np.array(data.shape[::-1]) + np.array([0, 1])) * np.array([col_width, row_height])
        fig, ax = plt.subplots(figsize=size)
        ax.axis('off')
    mpl_table = ax.table(cellText=data.values, bbox=bbox, colLabels=data.columns, **kwargs)
    mpl_table.auto_set_font_size(False)
    mpl_table.set_fontsize(font_size)
    for k, cell in mpl_table._cells.items():
        cell.set_edgecolor(edge_color)
        if k[0] == 0 or k[1] < header_columns:
            cell.set_text_props(weight='bold', color='w')
            cell.set_facecolor(header_color)
        else:
            cell.set_facecolor(row_colors[k[0]%len(row_colors) ])
    return ax.get_figure(), ax


def plot_risk_metrics_table(df: pd.DataFrame, 
                            fig_name: str, 
                            ) -> plt.Figure:
    fig_risk_metrics, ax_risk_metrics = render_mpl_table(df, header_columns=1, col_width=2.0)
    fig_risk_metrics.savefig(f'{FIGURES_DIR}/{fig_name}.png', bbox_inches='tight', dpi=FIG_DPI, transparent=True)
    return fig_risk_metrics


def plot_forecast_nav_overlay(df: pd.DataFrame,
                              fig: plt.Figure,
                              fig_name: str) -> plt.Figure:
    nav_line = plt.gca().lines[0]  # get the first line, there might be more
    df = df.mul(nav_line.get_ydata()[-1])
    quantiles = [0, 0.05, 0.25, 0.5, 0.75, 0.95, 1]
    q_df = df.quantile(quantiles, axis=1).T
    alphas = [None, 0.2, 0.5, 1, 1, 0.5, 0.2]
    for n, alpha in enumerate(alphas):
        plt.plot(q_df.index, q_df.iloc[:, n], alpha=0)
        if n == 0:
            continue
        plt.fill_between(q_df.index, q_df.iloc[:, n - 1], q_df.iloc[:, n], color=CustomColors.RED.to_scaled(), alpha=alpha)
    if max(nav_line.get_ydata().max(), q_df.max().max()) / min(nav_line.get_ydata().min(), q_df.max().min()) > 100:
        plt.yscale('log')
    fig.savefig(f'{FIGURES_DIR}/{fig_name}.png', bbox_inches='tight', dpi=FIG_DPI, transparent=True)
    return fig


def scatter_plot(df: pd.DataFrame,
                 x_column: str,
                 y_column: str,
                 hue_column: str = None,
                 x_label: str = None,
                 y_label: str = None,
                 fig_title: str = 'hue',
                 fig_name: str = 'scatter_plot',
                 to_percent: bool = False) -> plt.Figure:
    scale = 1.0
    if to_percent:
        scale = 100.
    fig, ax = plt.subplots(figsize=(6, 6))
    colors = {}
    for n, col in enumerate(CustomColors):
        try:
            colors[list(set(df[hue_column]))[n]] = col.to_scaled()
        except IndexError:
            continue
    ax.scatter(scale * df[x_column], scale * df[y_column], c=df[hue_column].map(colors), alpha=0.65)
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    # add a legend
    handles = [Line2D([0], [0], marker='o', color='w', markerfacecolor=v, label=k, markersize=8) for k, v in colors.items()]
    ax.legend(title=fig_title, handles=handles, bbox_to_anchor=(1.05, 1), loc='upper left')
    fig.savefig(f'{FIGURES_DIR}/{fig_name}.png', bbox_inches='tight', dpi=FIG_DPI, transparent=True)
    return fig


def plot_histogram(df: pd.DataFrame,
                   x_label: str = None,
                   y_label: str = None,
                   n_bins: int = 10,
                   is_density: bool = False,
                   fig_name: str = 'distribution_histogram') -> plt.Figure:
    if is_density:
        scale = df.shape[0]
    else:
        scale = 1.
    # create an array of subplots with a square shape
    n_rows = int(np.ceil(np.sqrt(df.shape[1])))
    n_cols = int(np.ceil(np.sqrt(df.shape[1])))
    # adjust the number of rows to ensure there are as least as possible unused boxes
    if (n_rows - 1) * n_cols >= df.shape[1]:
        n_rows -= 1
    # these are matplotlib.patch.Patch properties
    props = dict(boxstyle='round', facecolor='wheat', alpha=0.5)
    fig, axs = plt.subplots(n_rows, n_cols, sharex=True, sharey=True)
    for i, col in enumerate(df.columns):
        axs[i // n_cols, i % n_cols].hist(df[col].dropna() / scale, bins=n_bins)
        axs[i // n_cols, i % n_cols].set_title(col, fontsize=8)
        mu = df[col].mean()
        median = np.median(df[col])
        sigma = df[col].std()
        textstr = '\n'.join((
            r'$\mu=%.2f$' % (mu, ),
            r'$\mathrm{median}=%.2f$' % (median, ),
            r'$\sigma=%.2f$' % (sigma, )))
        # place a text box in upper left in axes coords
        axs[i // n_cols, i % n_cols].text(0.35, 0.9, textstr, transform=axs[i // n_cols, i % n_cols].transAxes, fontsize=6, verticalalignment='top', bbox=props)

    for ax in axs.flat:
        ax.set(xlabel=x_label, ylabel=y_label)
    # Hide x labels and tick labels for top plots and y ticks for right plots.
    for ax in axs.flat:
        ax.label_outer()
    fig.savefig(f'{FIGURES_DIR}/{fig_name}.png', bbox_inches='tight', dpi=FIG_DPI, transparent=True)
    return fig
