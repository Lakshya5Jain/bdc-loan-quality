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
uv run soi prices                  # yfinance for public tickers
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
- Amendments (10-K/A, 10-Q/A) and 10-Q prior-year-end comparatives duplicate periods; dedupe per
  (cik, period_end) keeping the latest `filed`.
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
  `market.screen`.
- Only trust a BDC-period when `core.reconciliation.coverage` is 0.85-1.15 (`data_ok` in signals).
