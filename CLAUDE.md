# BDC Loan-Quality Tracker

Track every BDC portfolio loan quarter over quarter from SEC XBRL data, roll deterioration up to a
BDC-level quality picture, and overlay stock price / NAV to find shorts and longs.

Note: the project directory name ends with a trailing space
(`.../soi (Schedule of Investments) `). Never `cd` into a hand-typed path; the shell starts here.

## Stack
- Python 3.12 via `uv` (`uv sync --extra dev`), package in `src/soi`, CLI entry `soi`.
- DuckDB single file at `data/soi.duckdb`; raw downloads under `data/raw/` (gitignored).
- FastAPI app in `api/` (`uv run soi serve` → http://127.0.0.1:8000).
- React + Vite + TS in `web/` (`npm --prefix web run dev` → http://localhost:5173, proxies `/api`).

## Commands
```
uv run soi ingest --all            # download + load all SEC BDC bulk zips
uv run soi ingest --since 2025q3   # only files at/after that name
uv run soi build holdings|loans|signals|screen|all
uv run soi prices                  # yfinance for public tickers + market.nav
uv run soi ixfootnotes --since 2026-06-30   # footnote links from the filings (non-accrual, PIK)
uv run soi ixfacts                 # holdings facts from the filings where bulk data misses rows
uv run soi backtest                # walk-forward backtest of BDC-level signals on public stock returns
uv run soi build lab               # strategy variants (floors, borrow cost, residual, extra inputs); needs backtest first
uv run soi build stale|forced|neighbors   # stale marks, forced-seller flags, neighbours of distress (also in build all)
uv run soi serve                   # or: uv run uvicorn api.main:app --port 8000
npm --prefix web run dev           # http://localhost:5173
uv run pytest                      # parser unit tests + smoke tests against data/soi.duckdb
uv run ruff check src api tests
```
Set `SEC_USER_AGENT="Name email"` in `.env` (SEC requires it).

## Data source facts
- SEC BDC Data Sets: https://www.sec.gov/data-research/sec-markets-data/bdc-data-sets
  Files: `.../business-development-company-bdc-data-sets/{2022q4..2025q4}_bdc.zip`, then
  `{2026_01..}_bdc.zip` monthly. Named by *filing* period, not fiscal period.
- Each zip: `datasets/{sub,num,txt,tag,pre,cal,non}.tsv` plus `soi.tsv` (pivot we do not rely on).
- Per-holding facts carry `InvestmentIdentifierAxis(us-gaap/YYYY)=<free text>()` in `segments`.
  Numeric tags in `num.tsv`; dates (maturity, acquisition) in `txt.tsv`.
- Non-accrual / PIK / amendment info is in the `footnote` column of `num.tsv`.
- ~13 public filers (HRZN, KBDC, OXSQ, PFX, PSBD, SAR, SuRo/NSLR, TPVG to 2025, PIAC, EQS, SLRC and
  TCPC early on) tag holdings as explicit axis-member combinations (issuer member x type member x
  industry member) with no `InvestmentIdentifierAxis`; `holdings.py` builds those rows from the
  leaf member sets (`src = 'member'` / `'member_fill'`).
- `num.tsv` carries facts in EUR/GBP/SEK etc. next to the USD fact (TSLX); only `uom = 'USD'` counts.
- Reported totals may only exist as `InvestmentsFairValueDisclosure`, a `TotalInvestmentsMember`
  fact, or the sum over an affiliation / ownership axis; `core.reconciliation.total_source` says which.
- The bulk data set drops 8-12% of GSBD's rows every quarter since 2025 (they are in the filing's
  inline XBRL). `soi ixfacts` reads facts from the filing for filings whose coverage is 50-97%
  (`raw.ix_facts`); `holdings.py` uses them instead of the bulk facts when they list more holdings.
- The bulk data writes en/em dashes as hyphens; inline-XBRL text is normalised the same way.
- Untagged industry names sit between the category heads and the issuer for some filers (Trinity):
  `_learn_industry_phrases` in `holdings.py` learns a phrase that precedes >= 3 issuers of one BDC.
- Amendments (10-K/A, 10-Q/A) and 10-Q prior-year-end comparatives duplicate periods; dedupe per
  (cik, period_end) preferring the filing whose detail reconciles, then the filing's own period,
  then the latest `filed`. Prior-period dates with < 25% of the BDC's usual row count are
  affiliate roll-forward tables and are dropped (`core.period_source`).
- BDC master list: business-development-company-YYYY.csv; CIK→ticker: company_tickers.json.

## Layout
- `src/soi/ingest/` download + raw load; `transform/` holdings pivot, identifier parsing, loan linking;
  `signals/` loan flags, BDC rollups, market screen; `db.py` schema + connection; `cli.py`.
- `api/main.py` endpoints read DuckDB read-only via `api/db.py` (per-request cursor);
  `web/src/pages` Screener, BdcList, BdcDetail, LoanDetail, Borrowers, BorrowerDetail, Status.
- Derived tables: `core.holdings` (one row per holding-period, subtotals/JV rows removed; see
  `core.holdings_excluded`, `core.holdings_jv`, `core.reconciliation`), `core.loans` + `core.loan_history`
  (loan_id stable across quarters), `signals.loan_quarter`, `signals.bdc_quarter`, `signals.bdc_latest`,
  `signals.migration`, `signals.borrower_marks`, `signals.bdc_generosity`, `market.prices`, `market.nav`,
  `market.screen`, `signals.bdc_fundamentals` (NII, coverage, leverage, NAV trend), `signals.bt_*` (backtest),
  `signals.stale_*` (lender disagreement, generosity), `signals.lab_*` (strategy variants), `signals.forced_seller*`
  (asset coverage flags), `signals.nbr_*` (neighbours of distress). All additive: the default strategy tables never change.
- Only trust a BDC-period when `core.reconciliation.coverage` is 0.9-1.1 or `override_note` is set (`data_ok` in signals).
