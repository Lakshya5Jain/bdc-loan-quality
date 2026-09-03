"""Daily prices and dividends via yfinance; NAV per share from XBRL."""
from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

from soi.config import settings


def public_tickers(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT ticker FROM ref.bdc_master WHERE is_public AND has_xbrl AND ticker IS NOT NULL ORDER BY ticker"
    ).fetchall()
    return [r[0] for r in rows]


def fetch_prices(con: duckdb.DuckDBPyConnection, tickers: list[str] | None = None,
                 start: str = "2021-01-01") -> str:
    import yfinance as yf

    settings.ensure_dirs()
    tickers = tickers or public_tickers(con)
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(start=start, auto_adjust=False, actions=True)
        except Exception:  # noqa: BLE001
            failed.append(t)
            continue
        if hist is None or hist.empty:
            failed.append(t)
            continue
        hist = hist.reset_index()
        hist["date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None).dt.date
        df = pd.DataFrame(
            {
                "ticker": t,
                "date": hist["date"],
                "close": hist["Close"].astype(float),
                "adj_close": hist.get("Adj Close", hist["Close"]).astype(float),
                "volume": hist.get("Volume", 0).astype(float),
                "dividend": hist.get("Dividends", 0.0).astype(float),
            }
        )
        frames.append(df)
    if not frames:
        return f"no price data fetched (failed: {failed})"
    prices = pd.concat(frames, ignore_index=True)
    prices.to_parquet(settings.raw_prices_dir / f"prices_{dt.date.today():%Y%m%d}.parquet")
    con.register("prices_df", prices)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS market.prices (
            ticker VARCHAR, date DATE, close DOUBLE, adj_close DOUBLE, volume DOUBLE, dividend DOUBLE
        )
        """
    )
    con.execute("DELETE FROM market.prices WHERE ticker IN (SELECT DISTINCT ticker FROM prices_df)")
    con.execute("INSERT INTO market.prices SELECT ticker, date, close, adj_close, volume, dividend FROM prices_df")
    build_nav(con)
    n = con.execute("SELECT count(*), count(DISTINCT ticker) FROM market.prices").fetchone()
    return f"market.prices: {n[0]:,} rows for {n[1]} tickers; failed: {failed}"


def build_nav(con: duckdb.DuckDBPyConnection) -> None:
    """NAV per share and net assets per (cik, period_end) from undimensioned XBRL facts,
    taking the value from the latest filing that reports that period."""
    con.execute(
        """
        CREATE OR REPLACE TABLE market.nav AS
        WITH facts AS (
            SELECT s.cik, n.ddate AS period_end, n.tag, n.value, s.filed
            FROM raw.num n JOIN raw.sub s USING (adsh)
            WHERE n.qtrs = 0 AND (n.segments IS NULL OR n.segments = '')
              AND n.tag IN ('NetAssetValuePerShare', 'StockholdersEquity', 'NetAssets',
                            'InvestmentOwnedAtFairValue', 'InvestmentOwnedAtCost', 'Assets',
                            'LongTermDebt', 'DebtInstrumentCarryingAmount',
                            'CommonStockSharesOutstanding', 'SharesOutstanding')
        ),
        latest AS (
            SELECT cik, period_end, tag, arg_max(value, filed) AS value
            FROM facts GROUP BY 1, 2, 3
        )
        SELECT cik, period_end,
               coalesce(
                   max(value) FILTER (WHERE tag = 'NetAssetValuePerShare'),
                   coalesce(max(value) FILTER (WHERE tag = 'StockholdersEquity'),
                            max(value) FILTER (WHERE tag = 'NetAssets'))
                   / nullif(coalesce(max(value) FILTER (WHERE tag = 'CommonStockSharesOutstanding'),
                                     max(value) FILTER (WHERE tag = 'SharesOutstanding')), 0)
               ) AS nav_per_share,
               coalesce(max(value) FILTER (WHERE tag = 'StockholdersEquity'),
                        max(value) FILTER (WHERE tag = 'NetAssets')) AS net_assets,
               coalesce(max(value) FILTER (WHERE tag = 'CommonStockSharesOutstanding'),
                        max(value) FILTER (WHERE tag = 'SharesOutstanding')) AS shares_outstanding,
               max(value) FILTER (WHERE tag = 'InvestmentOwnedAtFairValue') AS investments_fv,
               max(value) FILTER (WHERE tag = 'InvestmentOwnedAtCost') AS investments_cost,
               max(value) FILTER (WHERE tag = 'Assets') AS total_assets,
               coalesce(max(value) FILTER (WHERE tag = 'LongTermDebt'),
                        max(value) FILTER (WHERE tag = 'DebtInstrumentCarryingAmount')) AS debt
        FROM latest GROUP BY 1, 2
        """
    )
