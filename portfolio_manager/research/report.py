"""Report of the research: every strategy backtested and tested, every family evaluated out of sample, and the test of
uncovered interest parity; written to results/taa_research.xlsx and saved as tables (derived results) for the
dashboard.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from portfolio_manager.config.settings import RESULTS_DIR
from portfolio_manager.research.evaluation import evaluate_families, family_table, stability
from portfolio_manager.research.returns import ResearchData
from portfolio_manager.research.statistics import strategy_tests, uncovered_interest_parity
from portfolio_manager.research.strategies import run_strategies
from portfolio_manager.storage.tables import derived_folder, save_tables

logger = logging.getLogger(__name__)

REPORT_FILE = os.path.join(RESULTS_DIR, 'taa_research.xlsx')
DERIVED_KIND, DERIVED_NAME = 'taa_research', 'latest'
PERCENT_COLUMNS = {
    'excess_return',
    'excess_return_high_costs',
    'volatility',
    'max_drawdown',
    'benchmark_return',
    'alpha',
    'wf_excess_return',
    'wf_alpha',
    'combo_excess_return',
    'combo_alpha',
}
NOTES = [
    (
        'Returns',
        'Excess returns over cash, each instrument in its own currency, as if hedged: the same for any investor.',
    ),
    (
        'Trading',
        'Signals at the close of the last business day of each period, traded at the next close, 0.10% per '
        'trade; weights drift with prices until the next trade.',
    ),
    (
        'TS / XS',
        'Time series: each instrument on its own (held while its signal is positive, in cash otherwise). '
        'Cross section: the top third of the universe by signal (short the bottom third for long/short).',
    ),
    (
        'signal-weighted',
        'Positions proportional to the signal scaled to an average size of 1 and capped at 2 (Baltas and Kosowski '
        '2013; Carver 2015), the whole share of the instrument at the cap. Risk-adjusted momentum and reversal: the '
        'return divided by its expected volatility (Moskowitz, Ooi and Pedersen 2012). EWMAC: exponential moving-'
        'average crossover over the price volatility (Carver 2015). Golden cross: 50/200-day averages (Brock, '
        'Lakonishok and LeBaron 1992). Bollinger z-score: distance from the 20-day average (Bollinger 2001).',
    ),
    (
        'daily / weekly',
        'Signals checked every day (moving averages) or week (reversal); daily strategies trade a position only when '
        'it is 10% of its share away from the target (no-trade band, Carver 2015).',
    ),
    (
        'excess_return_high_costs',
        'Excess return with trades costing 0.25% instead of 0.10% (about: 0.15% times the turnover per year).',
    ),
    (
        'Stocks',
        "Today's members of the S&P 500, SMI and Euro Stoxx 50 only: the companies that left the index are missing "
        '(survivorship bias), which flatters long-only results. Top and bottom fifth; sector-neutral: ranked within '
        'each sector.',
    ),
    (
        'Benchmark',
        'Long-only: every instrument of the universe in equal shares. Currency hedging: never hedged. '
        'Long/short: cash.',
    ),
    ('sharpe', 'Annualized Sharpe ratio of the excess returns.'),
    (
        'signal_t',
        'Does the signal predict the next period? t-statistic of the pooled regression (TS) or Fama-MacBeth (XS).',
    ),
    (
        'alpha, beta, alpha_t',
        'Long-only and hedging: regression of the monthly returns on the benchmark. Alpha is the '
        'return beyond the average exposure (per year), the measure of skill of a timing strategy.',
    ),
    ('excess_t', 't-statistic of the mean monthly excess return: the skill of a long/short strategy.'),
    (
        'skill_p, skill_p_holm',
        'One-sided p-value of the skill (alpha, or excess return for long/short), and the same '
        'adjusted for the number of strategies tried (Holm): below 0.05, unlikely to be luck.',
    ),
    (
        'deflated_sharpe',
        'Probability that the skill is above what the best of as many strategies without skill would '
        'show by chance (above 0.95: convincing).',
    ),
    (
        'skill_sharpe halves',
        'Sharpe ratio of the skill returns in the first and second half of the history: a robust result holds in both.',
    ),
    (
        'Families',
        'Variants of one idea (e.g. momentum lookbacks) on one universe, built and traded the same way. wf_: each '
        'year the variant with the best skill so far, out of sample. combo_: all the variants in equal parts.',
    ),
    (
        'Interest parity',
        'Fama regression of each currency against the dollar: parity means beta = 1 (the higher rate '
        'is offset by a depreciation); beta below 1 is what carry earns.',
    ),
]


@dataclass
class ResearchReport:
    """The tables of the research and when they were computed."""

    strategies: pd.DataFrame
    families: pd.DataFrame
    parity: pd.DataFrame
    created_at: datetime


def build_report(data: ResearchData | None = None) -> ResearchReport:
    """Backtest, test and evaluate every default strategy (on the stored data if none is given)."""
    results = run_strategies(data=data)
    strategies = pd.concat([results.table(), strategy_tests(results), stability(results)], axis=1)
    families = family_table(results, evaluate_families(results))
    return ResearchReport(strategies, families, uncovered_interest_parity(results), datetime.now())


def _write_sheet(writer: pd.ExcelWriter, name: str, df: pd.DataFrame, index: bool = False):
    df.to_excel(writer, sheet_name=name, index=index, float_format='%.4f')
    sheet = writer.sheets[name]
    sheet.freeze_panes = 'B2' if index else 'A2'
    columns = ([df.index.name or ''] if index else []) + list(df.columns)
    for n, column in enumerate(columns, start=1):
        letter = sheet.cell(row=1, column=n).column_letter
        values = [str(column)] + [str(v) for v in (df.index if index and n == 1 else df[column]).tolist()[:200]]
        sheet.column_dimensions[letter].width = min(max(len(v) for v in values) + 2, 60)
        if column in PERCENT_COLUMNS:
            for cell in sheet[letter][1:]:
                cell.number_format = '0.0%'


def write_report(report: ResearchReport, path: str = REPORT_FILE):
    """Write the report as an Excel file: families, strategies, interest parity and notes on the columns."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    strategies = report.strategies.assign(start=report.strategies['start'].dt.date)
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        _write_sheet(writer, 'Families', report.families)
        _write_sheet(writer, 'Strategies', strategies)
        _write_sheet(writer, 'Interest parity', report.parity, index=True)
        notes = pd.DataFrame(NOTES, columns=['Item', 'Meaning'])
        notes.loc[len(notes)] = ['Computed', report.created_at.strftime('%Y-%m-%d %H:%M')]
        _write_sheet(writer, 'Notes', notes)


def save_report(report: ResearchReport):
    """Save the tables of the report (derived results), for the dashboard."""
    save_tables(
        derived_folder(DERIVED_KIND, DERIVED_NAME),
        {'strategies': report.strategies, 'families': report.families, 'parity': report.parity},
        attributes={'created_at': report.created_at.isoformat(timespec='seconds')},
    )


def run_research() -> ResearchReport:
    """Build the report from the stored data, write it and save its tables."""
    report = build_report()
    write_report(report)
    save_report(report)
    convincing = report.strategies[report.strategies['skill_p_holm'] < 0.05]
    logger.info(
        'TAA research: %d strategies, %d families; %d with significant skill after adjusting for the number tried. '
        'Report: %s',
        len(report.strategies),
        len(report.families),
        len(convincing),
        REPORT_FILE,
    )
    return report
