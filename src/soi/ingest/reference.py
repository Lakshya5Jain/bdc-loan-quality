"""Reference data: SEC BDC master list and CIK -> ticker map."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import httpx

from soi.config import BDC_REPORT_URL, COMPANY_TICKERS_URL, settings

# Manual fixes where the SEC ticker file's first entry is not the common stock.
TICKER_OVERRIDES: dict[int, str] = {}
# CIKs the SEC ticker file lists although their shares do not trade on an exchange
# (non-traded funds whose "ticker" is a fund code), so they are not screened against a price.
NOT_EXCHANGE_TRADED: frozenset[int] = frozenset({
    1923622,  # PGIM Private Credit Fund ("PGIM"): continuously offered, no listing
})


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": settings.sec_user_agent}, timeout=60,
                        follow_redirects=True)


def fetch_bdc_report(year: int | None = None, force: bool = False) -> Path:
    settings.ensure_dirs()
    year = year or dt.date.today().year
    out = settings.raw_ref_dir / f"business-development-company-{year}.csv"
    if out.exists() and not force:
        return out
    with _client() as c:
        r = c.get(BDC_REPORT_URL.format(year=year))
        if r.status_code == 404 and year == dt.date.today().year:
            r = c.get(BDC_REPORT_URL.format(year=year - 1))
        r.raise_for_status()
        out.write_bytes(r.content)
    return out


def fetch_company_tickers(force: bool = False) -> Path:
    settings.ensure_dirs()
    out = settings.raw_ref_dir / "company_tickers.json"
    if out.exists() and not force:
        return out
    with _client() as c:
        r = c.get(COMPANY_TICKERS_URL)
        r.raise_for_status()
        out.write_bytes(r.content)
    return out


def build_reference(con: duckdb.DuckDBPyConnection, force_download: bool = False) -> int:
    """Create ref.bdc_master(cik, file_no, name, ticker, exchange, is_public) from SEC files
    plus any filer seen in raw.sub."""
    report = fetch_bdc_report(force=force_download)
    tickers = fetch_company_tickers(force=force_download)

    tk = json.loads(tickers.read_text())
    # company_tickers.json is ordered by market cap; the first entry per CIK is the common stock,
    # later ones are baby bonds / preferreds (e.g. HCXY, SAJ).
    rows = [(int(k), int(v["cik_str"]), v["ticker"], v.get("title", "")) for k, v in tk.items()]
    con.execute(
        "CREATE OR REPLACE TABLE ref.company_tickers (idx INTEGER, cik BIGINT, ticker VARCHAR, title VARCHAR)"
    )
    con.executemany("INSERT INTO ref.company_tickers VALUES (?,?,?,?)", rows)
    con.execute("CREATE OR REPLACE TABLE ref.ticker_overrides (cik BIGINT, ticker VARCHAR)")
    if TICKER_OVERRIDES:
        con.executemany(
            "INSERT INTO ref.ticker_overrides VALUES (?,?)", list(TICKER_OVERRIDES.items())
        )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE ref.bdc_report AS
        SELECT "File_No" AS file_no, try_cast("CIK" AS BIGINT) AS cik,
               "Registrant_Name" AS name, "City" AS city, "State" AS state,
               "Filing Date" AS last_filing_date, "Filing Type" AS last_filing_type
        FROM read_csv('{report}', header=true, all_varchar=true)
        """
    )
    not_listed = ", ".join(str(c) for c in sorted(NOT_EXCHANGE_TRADED)) or "-1"
    con.execute(
        f"""
        CREATE OR REPLACE TABLE ref.bdc_master AS
        WITH filers AS (
            SELECT cik, any_value(name ORDER BY filed DESC) AS name,
                   any_value(fileNumber ORDER BY filed DESC) AS file_no,
                   max(filed) AS last_filed
            FROM raw.sub GROUP BY cik
        ),
        listed AS (
            SELECT c.cik, coalesce(o.ticker, any_value(c.ticker ORDER BY c.idx)) AS ticker
            FROM ref.company_tickers c LEFT JOIN ref.ticker_overrides o ON c.cik = o.cik
            GROUP BY c.cik, o.ticker
        ),
        universe AS (
            SELECT coalesce(f.cik, r.cik) AS cik,
                   coalesce(f.name, r.name) AS name,
                   coalesce(f.file_no, r.file_no) AS file_no,
                   f.last_filed
            FROM filers f FULL OUTER JOIN ref.bdc_report r ON f.cik = r.cik
        )
        SELECT u.cik, u.name, u.file_no, u.last_filed, l.ticker,
               l.ticker IS NOT NULL AND u.cik NOT IN ({not_listed}) AS is_public,
               u.cik IN (SELECT cik FROM raw.sub) AS has_xbrl
        FROM universe u LEFT JOIN listed l ON u.cik = l.cik
        WHERE u.cik IS NOT NULL
        ORDER BY u.name
        """
    )
    return con.execute("SELECT count(*) FROM ref.bdc_master").fetchone()[0]
