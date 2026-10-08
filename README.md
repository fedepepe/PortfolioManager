# Portfolio Manager

A dashboard to track, backtest and optimize DeGiro portfolios.

It downloads the trades, cash movements and prices of one or more DeGiro accounts, rebuilds the history of each
portfolio, compares it with a benchmark, and computes optimized portfolios on the same instruments.

- **Portfolio** page: NAV against the benchmark, allocation, performance and risk metrics, correlations, risk
  contributions, monthly returns, adjusted prices of the instruments held, and a price chart of any instrument.
- **Instruments** page: performance metrics and correlations of the ETF catalog.
- **Strategies** page: optimized portfolios (max Sharpe, min variance, max return, risk parity, equal weight) with
  configurable constraints, backtested against the real portfolio.

## Setup

Requires Python 3.10.

```bash
python -m venv venv
venv\Scripts\activate                      # Windows; on Linux/macOS: source venv/bin/activate
pip install -r requirements-dev.txt        # app + test and lint tools (requirements.txt: app only)
```

### Accounts and credentials

Accounts are defined in `portfolio_manager/config/accounts.py`: name, base currency, credentials file and benchmark.

Each account needs a JSON credentials file in the `credentials/` folder (never committed), with the fields expected by
[degiro-connector](https://github.com/Chavithra/degiro-connector):

```json
{
  "username": "...",
  "password": "...",
  "int_account": 1234567,
  "totp_secret_key": "..."
}
```

`totp_secret_key` is needed with two-factor authentication. If the environment variable `DEGIRO_ACCOUNT` is set,
the library reads the credentials from it instead of the file, for every account.

## Running

```bash
python main.py
```

Then open <http://127.0.0.1:8000/portfolio_manager>. At start-up the dashboard only reads the data saved by previous
runs; new data is downloaded only with the **Update** button (Portfolio page) and optimizations run only with **Run
optimization** (Strategies page).

### Maintenance tasks

`tasks.py` runs the same jobs from the command line, plus the long catalog downloads:

```bash
python tasks.py --help
python tasks.py update                         # like the Update button, for every account
python tasks.py optimize --account degiro_eur  # rerun the saved optimization of one account
python tasks.py etf-performance --isin IE00B4L5Y983
```

## Data

Nothing in these folders is committed:

| Folder | Content |
|---|---|
| `data/` | the SQLite database (`degiro.db`), the DeGiro downloads and the backtests (Excel) |
| `results/` | performance results and the ETF catalogs (Excel) |
| `state/` | per-account state |
| `credentials/` | the DeGiro credentials |

Adjusted prices come from Yahoo Finance when it is reachable and are stored in the database, so that the backtests
also work offline. DeGiro cash movements are recognized by their descriptions, which are in Italian (the language of
the accounts): see `portfolio_manager/backtest/workflows.py`.

## Code layout

```
main.py                    web server (FastAPI) serving the dashboard
tasks.py                   command-line maintenance tasks
portfolio_manager/
├── config/                accounts, paths and constants, account state
├── degiro/                DeGiro API: connection, products, transactions, price charts
├── market_data/           Yahoo Finance
├── storage/               database (models, queries) and Excel files
├── backtest/              portfolio model, backtest engine, account/benchmark/optimized workflows
├── analytics/             performance metrics, instrument and ETF catalog analysis
├── optimization/          optimization settings, optimizer, objective functions
├── dashboard/             Dash app: pages, figures, data service
└── utils/
tests/                     pytest tests (synthetic data only)
```

## Development

```bash
python -m pytest           # tests
ruff check .               # lint (ruff check --fix . for the safe fixes)
ruff format .              # format
```

Settings are in `pyproject.toml` (120-character lines, single quotes). Mechanical reformatting commits are listed in
`.git-blame-ignore-revs`; enable it with `git config blame.ignoreRevsFile .git-blame-ignore-revs`.
