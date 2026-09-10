"""Forced-seller flags: how close each BDC is to the 150% asset-coverage minimum, and whether
being close while stress is rising predicts selling loans at a loss.

Asset coverage = (net assets + debt) / debt, the ratio the 1940 Act caps at a 150% minimum for
BDCs that elected the reduced requirement. Below it a BDC cannot borrow more or pay dividends,
so it must raise equity or sell assets. Filers tag the ratio directly in about a third of
quarters (InvestmentCompanySeniorSecurityIndebtednessAssetCoverageRatio and the older
InvestmentCompanyAssetCoverageRatio); elsewhere it is computed from the balance-sheet tags
already in signals.bdc_fundamentals. Tagged values of exactly 1.50 or 2.00 are the legal
minimum being tagged, not the actual ratio, and are ignored.

Flag = coverage within 20 points of 150% (coverage <= 1.70) AND the share of debt below 90
rose in each of the last two quarters.

Test = flagged versus unflagged BDC-quarters on the rate of loans exiting the book at a loss
(last mark below 0.90) over the next two quarters, as a share of the debt book at cost. Mean
rates, ratio, and a bootstrap confidence interval on the difference.

Tables: signals.forced_seller (per BDC-quarter), signals.forced_seller_test (one row per
comparison), all new.
"""
from __future__ import annotations

import random

import duckdb

MIN_COVERAGE = 1.50
NEAR_POINTS = 20          # within this many points of the minimum
BOOTSTRAP = 2000


def build_forced_sellers(con: duckdb.DuckDBPyConnection, log=print) -> str:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.forced_seller AS
        WITH tagged AS (
            SELECT f.cik, n.ddate AS period_end, arg_max(n.value, f.filed) AS v
            FROM raw.num n JOIN core.filings f USING (adsh)
            WHERE n.tag IN ('InvestmentCompanySeniorSecurityIndebtednessAssetCoverageRatio',
                            'InvestmentCompanyAssetCoverageRatio')
              AND (n.segments IS NULL OR n.segments = '') AND n.qtrs = 0
              AND n.value BETWEEN 1.2 AND 6 AND n.value NOT IN (1.5, 2.0)
            GROUP BY 1, 2
        ),
        base AS (
            SELECT b.cik, b.period_end, b.data_ok, b.pct_debt_below_90, b.debt_cost,
                   t.v AS coverage_tagged,
                   CASE WHEN f.debt > 0 AND f.net_assets > 0
                             AND (f.net_assets + f.debt) / f.debt BETWEEN 1.2 AND 6
                        THEN (f.net_assets + f.debt) / f.debt END AS coverage_computed,
                   lag(b.pct_debt_below_90, 1) OVER w AS b90_1, lag(b.pct_debt_below_90, 2) OVER w AS b90_2,
                   lag(b.period_end, 2) OVER w AS pe_2
            FROM signals.bdc_quarter b
            LEFT JOIN tagged t USING (cik, period_end)
            LEFT JOIN signals.bdc_fundamentals f USING (cik, period_end)
            WHERE b.data_ok
            WINDOW w AS (PARTITION BY b.cik ORDER BY b.period_end)
        ),
        cov AS (
            SELECT *,
                   coalesce(coverage_tagged, coverage_computed) AS coverage,
                   CASE WHEN coverage_tagged IS NOT NULL THEN 'tagged'
                        WHEN coverage_computed IS NOT NULL THEN 'computed' END AS coverage_source
            FROM base
        ),
        flagged AS (
            SELECT *,
                   (coverage - {MIN_COVERAGE}) * 100 AS distance_pts,
                   -- two consecutive rises, and the two prior quarters really are the prior two
                   (b90_1 IS NOT NULL AND b90_2 IS NOT NULL AND pct_debt_below_90 > b90_1 AND b90_1 > b90_2
                    AND pe_2 >= period_end - INTERVAL 200 DAY) AS b90_up_2q
            FROM cov
        )
        SELECT f.cik, f.period_end, f.coverage, f.coverage_source, f.distance_pts,
               f.distance_pts <= {NEAR_POINTS} AS near_limit, f.b90_up_2q,
               (f.distance_pts <= {NEAR_POINTS} AND f.b90_up_2q) AS flagged,
               f.pct_debt_below_90, f.debt_cost,
               -- a few positions carry a negative cost; a loss exit cannot be negative
               (greatest(coalesce(x1.exit_loss_cost, 0), 0) + greatest(coalesce(x2.exit_loss_cost, 0), 0)) / nullif(f.debt_cost, 0) AS exit_loss_fwd2,
               x1.period_end IS NOT NULL AND x2.period_end IS NOT NULL AS fwd2_observed
        FROM flagged f
        LEFT JOIN LATERAL (SELECT period_end, exit_loss_cost FROM signals.bdc_quarter x WHERE x.cik = f.cik AND x.data_ok
                             AND x.period_end BETWEEN f.period_end + INTERVAL 75 DAY AND f.period_end + INTERVAL 105 DAY
                           ORDER BY x.period_end LIMIT 1) x1 ON TRUE
        LEFT JOIN LATERAL (SELECT period_end, exit_loss_cost FROM signals.bdc_quarter x WHERE x.cik = f.cik AND x.data_ok
                             AND x.period_end BETWEEN f.period_end + INTERVAL 165 DAY AND f.period_end + INTERVAL 195 DAY
                           ORDER BY x.period_end LIMIT 1) x2 ON TRUE
        """
    )
    _test(con, log)
    n, n_cov, n_flag = con.execute(
        "SELECT count(*), count(coverage), count(*) FILTER (WHERE flagged) FROM signals.forced_seller"
    ).fetchone()
    return f"forced sellers: {n:,} BDC-quarters, coverage known for {n_cov:,}, {n_flag} flagged"


def _test(con: duckdb.DuckDBPyConnection, log) -> None:
    rows = con.execute(
        """
        SELECT s.flagged, s.near_limit, s.b90_up_2q, s.exit_loss_fwd2, m.is_public
        FROM signals.forced_seller s JOIN ref.bdc_master m USING (cik)
        WHERE s.fwd2_observed AND s.coverage IS NOT NULL AND s.exit_loss_fwd2 IS NOT NULL
        """
    ).fetchall()
    out: list[tuple] = []
    rng = random.Random(11)

    def compare(label: str, universe: list[tuple], pick) -> None:
        a = [r[3] for r in universe if pick(r)]
        b = [r[3] for r in universe if not pick(r)]
        if len(a) < 5 or len(b) < 5:
            return
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        diffs = []
        for _ in range(BOOTSTRAP):
            sa = [a[rng.randrange(len(a))] for _ in a]
            sb = [b[rng.randrange(len(b))] for _ in b]
            diffs.append(sum(sa) / len(sa) - sum(sb) / len(sb))
        diffs.sort()
        lo, hi = diffs[int(0.025 * BOOTSTRAP)], diffs[int(0.975 * BOOTSTRAP) - 1]
        out.append((label, len(a), ma, len(b), mb, ma - mb, lo, hi, ma / mb if mb > 0 else None,
                    sum(1 for d in diffs if d > 0) / BOOTSTRAP))
        log(f"  forced-seller test [{label}]: flagged {len(a)} at {ma:.2%} vs unflagged {len(b)} at {mb:.2%}; "
            f"diff {ma - mb:+.2%} (95% CI {lo:+.2%} to {hi:+.2%})")

    pub = [r for r in rows if r[4]]
    compare("public: flag (near limit and stress rising 2q)", pub, lambda r: r[0])
    compare("public: near limit only", pub, lambda r: r[1])
    compare("public: stress rising 2q only", pub, lambda r: r[2])
    compare("all BDCs: flag", rows, lambda r: r[0])
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.forced_seller_test (
            comparison VARCHAR, n_flagged INTEGER, rate_flagged DOUBLE, n_unflagged INTEGER, rate_unflagged DOUBLE,
            diff DOUBLE, ci_lo DOUBLE, ci_hi DOUBLE, ratio DOUBLE, p_diff_positive DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.forced_seller_test VALUES (?,?,?,?,?,?,?,?,?,?)", out)
