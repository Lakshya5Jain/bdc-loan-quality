# BDC Loan-Quality Tracker

Tracks every business development company (BDC) portfolio loan quarter over quarter from the
SEC's XBRL schedule-of-investments data, rolls loan deterioration up to a BDC-level quality
picture, and overlays stock price and NAV to find shorts and longs.

**Live site:** see the Vercel deployment linked from this repo's About section.

## What it does

- **Ingest**: downloads the SEC BDC bulk data sets (quarterly, then monthly) and loads them into
  a local DuckDB file.
- **Holdings**: pivots per-holding XBRL facts into one row per holding-period, removing
  subtotals and joint-venture double counting, and reconciles detail to reported totals.
- **Loans**: links the same loan across quarters into a stable `loan_id`, using identifier
  parsing plus fuzzy matching.
- **Signals**: per-loan flags (markdowns, non-accrual, PIK, spread and maturity changes),
  BDC-level rollups, migration matrices, and cross-BDC "generosity" (who marks the same borrower
  higher than peers).
- **Screen**: overlays yfinance prices and NAV to score public BDCs into long/short quadrants.

## Stack

- Python 3.12 via `uv`; package in `src/soi`, CLI entry `soi`.
- DuckDB single file at `data/soi.duckdb` (gitignored, about 2.7 GB with raw tables).
- FastAPI in `api/` for local use; React + Vite + TypeScript in `web/`.
- Public site is a static export on Vercel (no server; see below).

## Local setup

```
uv sync --extra dev
cp .env.example .env           # set SEC_USER_AGENT="Name email" (SEC requires it)
uv run soi ingest --all        # download + load all SEC BDC bulk zips
uv run soi build all           # holdings -> loans -> signals -> screen
uv run soi prices              # yfinance for public tickers
uv run soi serve               # API at http://127.0.0.1:8000
npm --prefix web install
npm --prefix web run dev       # UI at http://localhost:5173 (proxies /api)
uv run pytest
uv run ruff check src api tests
```

## Publishing the site

The public site has no backend. `uv run soi export-static` writes every API response the UI
needs as JSON under `web/public/data/` (about 6,700 files, 625 MB raw, 100 MB gzipped).
Because Vercel's Hobby plan caps CLI uploads at 100 MB, the tree is not uploaded with the app:
it is published as a `data.tar.gz` asset on the GitHub release tagged `data`, and the Vercel
build downloads and unpacks it (`web/scripts/fetch-data.sh`) before `vite build`.

One command does all of that after the local database changes:

```
scripts/publish.sh            # export -> upload release asset -> vercel deploy --prod
scripts/publish.sh --no-export   # reuse web/public/data
scripts/publish.sh --no-deploy   # refresh the bundle only
```

Requires `gh` (logged in with push access) and `vercel` (logged in, project linked from `web/`).

To run the UI locally against the static tree instead of FastAPI:

```
uv run soi export-static
npm --prefix web run dev:static
```

The client-side routing and decoding for static mode lives in `web/src/lib/api.ts`; the pages
are identical in both modes.

## Data notes

- Only trust a BDC-period when `core.reconciliation.coverage` is between 0.9 and 1.1, or `override_note` is set (a filing with no comparable reported total whose detail is 85-105% of total assets)
  (`data_ok` in the signals tables).
- Amendments and prior-year comparatives duplicate periods; the pipeline keeps, per
  (cik, period_end), the filing whose detail reconciles, then the filing's own period, then the
  latest filed. Prior-period dates that carry only a handful of rows (affiliate roll-forward
  tables) are dropped.
- Filers that tag holdings as axis-member combinations instead of the identifier axis
  (Horizon, Kayne Anderson BDC, Oxford Square, PhenixFIN, Palmer Square, Saratoga, SuRo,
  TriplePoint before 2025) are read from the leaf member sets; their identifiers look like
  `Issuer | Instrument | Industry`.
- Only USD facts are used; several filers also tag local-currency amounts of foreign loans.
- Where the SEC bulk data lists fewer holdings than the filing itself (Goldman Sachs BDC since
  2025), `uv run soi ixfacts` reads the facts from the filing's inline XBRL and the pipeline uses
  those for that filing-period.
- Loans are linked quarter to quarter by identifier text, then by issuer + instrument, then by
  issuer + debt/equity class, then by closest cost within an issuer, then fuzzy issuer names.
- Non-accrual, PIK and amendment details come from XBRL footnotes and are parsed heuristically.
