# Portfolio Manager

A dashboard to track Degiro portfolios and to build and backtest strategic asset allocations.

It downloads the trades, cash movements and prices of one or more Degiro accounts, rebuilds the history of each
portfolio, compares it with a benchmark, and backtests allocations of the classified ETF catalog.

- **Portfolio** page: NAV against the benchmark, allocation, performance and risk metrics, correlations, risk
  contributions, monthly returns, adjusted prices of the instruments held, and a price chart of any instrument.
- **Markets** page: the monthly returns of the last 12 months of sectors, world indices, bonds, commodities,
  currencies and crypto, as heatmaps.
- **Strategic Asset Allocation** page: an allocation built in four steps (asset class weights, sub-classes,
  instruments of each part with manual weights or weights optimized within the class ranges, backtest against the
  benchmark and the portfolio), saved per account under a name; an allocation with manual weights can become the
  benchmark of the account.
- **Accounts** page: the accounts, their connection and their benchmark.

## Setup

Requires Python 3.10.

```bash
python -m venv venv
venv\Scripts\activate                      # Windows; on Linux/macOS: source venv/bin/activate
pip install -r requirements-dev.txt        # app + test and lint tools (requirements.txt: app only)
```

### Accounts and credentials

Accounts are saved in the database: name (the portfolio name), broker (Degiro only so far), base currency and
credentials file. They are added and connected on the **Accounts** page of the dashboard (see "First run" below),
or from the command line:

```bash
python tasks.py add-account --name "Portfolio USD"   # credentials file: portfolio_usd.json
python tasks.py connect-account --account "Portfolio USD"   # base currency and currency pairs from Degiro
```

At each **Update** (and with `connect-account`) the base currency of the account and the currency pairs traded at
Degiro are read from the account information; the exchange rates use the direct pair of two currencies, otherwise a cross rate through
EUR or USD.

Once its currency is known, an account in CHF or EUR gets a default benchmark (see `portfolio_manager/config/account_settings.py`).

Each account needs a JSON credentials file in the `credentials/` folder (never committed), with the fields expected by
[degiro-connector](https://github.com/Chavithra/degiro-connector):

```json
{
  "username": "...",
  "password": "...",
  "totp_secret_key": "..."
}
```

`totp_secret_key` is needed with two-factor authentication; `int_account` (the account number) may be added, otherwise
it is read from Degiro at each login. If the environment variable `DEGIRO_ACCOUNT` is set,
the library reads the credentials from it instead of the file, for every account.

## Running

```bash
python main.py
```

Then open <http://127.0.0.1:8000/portfolio_manager>. Start-up and the pages only read the data saved by previous
runs; the Degiro data is downloaded only with the **Update** button (Portfolio page), which turns orange when the
prices of a newer trading day are available. The Yahoo Finance data of the listings in use (allocations, benchmarks,
portfolios, the instruments of the Tactical Asset Allocation research, then the catalog from the most traded) is brought
up to date in the background while the server runs: the prices of a trading day count as final from 08:00 of the next
day. The interest rates of the research (ECB, SNB, Bank of England, BIS, US Treasury yields) are updated once a day.
**Run backtest** (Strategic Asset Allocation page) offers to download the new prices of its instruments first. The
downloads started from the dashboard (Update, Connect, Run backtest, ...) come first: the background update pauses
until they end.

### First run

With no saved data, the dashboard opens on the **Accounts** page (also in the sidebar):

1. Choose the broker, type the portfolio name and press **Add account**.
2. Save the credentials of the account in the file shown (e.g. `credentials/portfolio_chf.json`, see above); the list
   says whether the file is found.
3. Press **Connect**: the base currency of the account and the currency pairs are read from Degiro, and an account in
   CHF or EUR gets its default benchmark.
4. On the **Portfolio** page, press **Update**: the first download of the transactions, cash movements and prices
   takes a few minutes; then the figures are drawn.

The **Strategic Asset Allocation** page chooses its instruments among the classified catalog funds, prepared by the
long catalog tasks below, in this order:

1. `fetch-product-catalog`: the Degiro products.
2. `map-etf-catalog` (Degiro login): the Yahoo Finance listing of every catalog product, i.e. every line of an ETF
   (exchange and currency) on Xetra, SIX, Milan and Amsterdam; see *Mapping* below.
3. `fetch-etf-catalog`: the Yahoo Finance history of the mapped listings.
4. `classify-etfs` (Degiro login): the asset class of each fund; see *Classification* below.
5. `build-etf-catalog`: the ranked catalog of each account (Excel copy in `results/`).

The catalog keeps the 250 funds with the highest average daily traded value (close x volume) of the last 3 months,
each in the currency of its listing; the values are compared at the latest exchange rates, so the ranking is the same
for every account. Each fund (ISIN) appears once, with its most traded line in the account currency, otherwise its
most traded line. The performance metrics of the catalog ETFs are computed in the currency of each account and saved in
the database (table `etf_catalog`), with an Excel copy in `results/`.

### Mapping

A Degiro product is mapped to the Yahoo Finance listing whose prices match its Degiro prices: over the last 3 months,
the median daily gap must be 2% or less, on at least 20 common days, after converting with the Degiro exchange rates.
Its own line (the Degiro symbol on the Yahoo exchange of its Degiro exchange, e.g. `EUNL.DE`) comes first; otherwise the
listings found on Yahoo by its ISIN or its symbol, on the preferred exchanges first, in its currency, with the closest
prices. There is no ISIN check: the ISIN that yfinance gives comes from a name search on Business Insider, often of
another security. Products whose Degiro prices are missing or more than 10 days old are not searched.

Yahoo reports a wrong currency for some listings (e.g. USD prices labelled CHF on SIX): a listing whose prices match
only in another currency is accepted in that currency ("own line (currency fixed)"), and the fix (table
`yahoo_currency_fixes`) is used wherever the currency of the listing matters (backtests, catalog). Fixes are checked
again at each search and removed when no product uses the listing any more.

Products not found are searched again after 30 days, found ones after 3 months (`--retry-not-found` and
`--remap-found-not-own-line` search them earlier, `--remap` searches every product again). The products traded in the
accounts keep their listing: `remap-portfolio-listings` shows what would change (and saves it with `--apply`). The
mapping, with the Degiro ISIN of each product, is in the table `degiro_yahoo_map`, with an Excel copy for review
(`results/yahoo_mapping.xlsx`).

### Classification

Each fund (ISIN) of the catalog gets a macro asset class (Equity, Fixed Income, Money Market, Commodities, Real Estate,
Crypto, Multi-Asset, Alternatives), a leveraged/inverse flag, a sub-class and a region:

| Class | Sub-class | Region |
|---|---|---|
| Equity | Broad, Sector, Thematic, Factor | Global, North America, Europe, Euro Area, Switzerland, UK, Japan, Asia Pacific, Emerging Markets |
| Fixed Income | Government, Corporate, High Yield, Inflation-linked, Aggregate, Short-term | the market of the bonds held (Euro Area, United States, Switzerland, UK, Global, Emerging Markets), not the currency the fund trades in |
| Commodities | Precious metals, Energy, Industrial metals, Agriculture, Broad | |
| Crypto | Bitcoin, Ethereum, Basket, Other coin | |
| Real Estate, Money Market | | region (currency market) |

The class comes from Degiro's ETF list (its asset allocation and ETP type) when it is clear, otherwise from rules on
the benchmark and the name; the region from Degiro's region, the benchmark or the name; currency hedged share classes
are recognized. Each value records its source. The classes are in the table `etf_classes`, with an Excel copy for
review (`results/etf_classes.xlsx`).

### Maintenance tasks

`tasks.py` runs the same jobs from the command line, plus the long catalog downloads:

```bash
python tasks.py --help
python tasks.py update                             # like the Update button, for every account
python tasks.py show-benchmark                     # the benchmark of each account
python tasks.py refresh-benchmark                  # recompute the benchmarks from the saved backtests
python tasks.py etf-performance --isin IE00B4L5Y983
python tasks.py map-etf-catalog                    # Yahoo listings of the catalog products due (see Mapping)
python tasks.py remap-portfolio-listings --apply   # search again the listings of the traded products
python tasks.py fetch-etf-catalog                  # ETF catalog history: about 2 h the first time, then new days only
python tasks.py classify-etfs                      # asset classes of the catalog funds
python tasks.py build-etf-catalog                  # catalog of each account
python tasks.py refresh-yahoo-history --full       # download every stored Yahoo Finance history again
python tasks.py refresh-markets                    # the data of the Markets page
python tasks.py fetch-taa-data [--full]            # TAA research: index members, instruments and stocks (whole history), rates
python tasks.py fetch-former-members               # S&P 500 changes and former members (survivorship research)
python tasks.py taa-research                       # backtest and test the TAA strategies: results/taa_research.xlsx
```

## Data

Nothing in these folders is committed:

| Folder | Content |
|---|---|
| `data/` | the SQLite database (`degiro.db`) with the Degiro downloads, an Excel copy of the downloads of each account (transactions, cash movements, products, prices, exchange rates), and an Excel copy of each backtest (`*_visual.xlsx`: tickers as column labels), for inspection |
| `data/derived/` | the working copy of the backtests and performance results (Parquet tables), read by the dashboard |
| `results/` | an Excel copy of the performance results, the ETF catalogs, the Yahoo mapping and the ETF classes, for inspection |
| `credentials/` | the Degiro credentials |

Results saved before the Parquet tables are converted with `python tasks.py convert-results` (until then they are read
from the Excel files).

Each **Update** adds the Degiro downloads to the database: transactions, cash movements and prices already stored are
kept even when Degiro no longer returns them (it returns ten years), and the product information is refreshed. A
backtest ends on the last day with the prices of all the products held, so a day that Degiro has only partly published
is left out. Degiro downloads saved as Excel files before the database are imported with
`python tasks.py import-degiro-excel`.

The settings chosen for each account are saved in the database, starting with its benchmark (instruments, weights,
rebalancing frequency); an account without saved settings starts with the default benchmark of
`portfolio_manager/config/accounts.py`.

The database records the version of its schema. At start-up an older database is upgraded by the steps in
`portfolio_manager/storage/migrations.py`, after a copy of it is saved next to it (`degiro.db.bak-v{version}`); a
database newer than the code stops the start-up without being changed.

Instrument prices come from Yahoo Finance and are stored in the database (daily open, high, low, close, volume,
dividends, splits and capital gains), so that the backtests also work offline; the information stored about each
listing is what Yahoo reports (the ISIN of a fund is the Degiro one, see *Mapping*). The Yahoo Finance listing of each
instrument is searched once; then each **Update** downloads only the new days of the portfolio and benchmark
instruments, and checks the last stored days again: if Yahoo changed them (a split, a correction), the whole history
of the listing is downloaded again. The adjusted close is computed from the close and the dividends, as Yahoo computes
it. Prices quoted in hundredths of a currency (pence: `GBp`) are converted. Degiro cash movements are recognized by their descriptions, which are in Italian (the language of
the accounts): see `portfolio_manager/backtest/workflows.py`.

## Code layout

```
main.py                    web server (FastAPI) serving the dashboard
tasks.py                   command-line maintenance tasks
portfolio_manager/
├── config/                accounts, account settings, paths and constants
├── degiro/                Degiro API: connection, products, transactions, price charts
├── market_data/           Yahoo Finance
├── storage/               database (models, queries, schema upgrades), Parquet tables and Excel files
├── backtest/              portfolio model, backtest engine, account/benchmark/allocation workflows
├── analytics/             performance metrics, ETF catalog, classification, allocation candidates, price updates
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
