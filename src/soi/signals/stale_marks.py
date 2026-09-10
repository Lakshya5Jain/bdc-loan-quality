"""Stale-mark detector: where lenders disagree about the same loan, and whether a BDC that
marks its shared borrowers above the other lenders pays for it later.

Unit of comparison: one borrower, one lien type (first lien, second lien, ...), one quarter,
held by two or more BDCs whose quarter passed the reconciliation gate. Each lender's position
is summed to one mark (fair value / cost). Comparing across lien types would mix a first lien
at par with a second lien at 60, so the split is by instrument type.

Tables (all new; nothing existing is touched):
  signals.stale_borrowers    one row per shared borrower-type-quarter: every lender's mark, the
                             gap between the highest and lowest, who is at each end
  signals.stale_bdc          per BDC-quarter: generosity (own cost-weighted mark on shared
                             borrowers minus the other lenders' cost-weighted mark on the same
                             loans), the no-second-opinion share (debt cost in borrowers no
                             other BDC holds), counts
  signals.stale_test_periods per calendar quarter and outcome: the most generous fifth of
                             public BDCs against the least generous fifth on what happened next
  signals.stale_test_summary per outcome: mean spread, t-stat, hit rate, mean rank correlation

The test is one-directional and point-in-time by construction: generosity in quarter t uses only
quarter-t filings; the outcomes are the NAV per share change and the change in the share of debt
below 90 from t to t+1 and t+2, found by date so a missing quarter does not shift the window.
"""
from __future__ import annotations

import duckdb

from soi.signals.backtest import QTR_SQL, _spearman, _tstat

MIN_SHARED = 5      # shared borrower-types a BDC needs for its generosity to mean anything
# a debt position marked outside this range is a unit error in the filing (a few private filers
# report fair value in dollars against cost in thousands), not a valuation opinion
MARK_LO, MARK_HI = 0.0, 1.25
MIN_NAMES = 10      # public BDCs a quarter needs to be tested
OUTCOMES = {
    # name: (column, expected sign of top-minus-bottom if generous marks are stale)
    "nav_chg_fwd1": ("nav_chg_fwd1", -1.0),
    "nav_chg_fwd2": ("nav_chg_fwd2", -1.0),
    "b90_chg_fwd1": ("b90_chg_fwd1", 1.0),
    "b90_chg_fwd2": ("b90_chg_fwd2", 1.0),
}


def build_stale_marks(con: duckdb.DuckDBPyConnection, log=print) -> str:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.stale_borrowers AS
        WITH h AS (
            SELECT q.issuer_norm AS borrower_key, q.cik, q.period_end, q.instrument_type,
                   any_value(q.issuer_name) AS issuer_name, any_value(q.industry) AS industry,
                   sum(q.fair_value) AS fv, sum(q.cost) AS cost,
                   sum(q.fair_value) / sum(q.cost) AS mark,
                   bool_or(q.nonaccrual_flag) AS nonaccrual
            FROM signals.loan_quarter q
            WHERE q.is_debt AND q.data_ok AND q.issuer_norm <> '' AND length(q.issuer_norm) >= 4
              AND q.cost > 0 AND q.fair_value IS NOT NULL
              AND q.fair_value / q.cost BETWEEN {MARK_LO} AND {MARK_HI}
            GROUP BY 1, 2, 3, 4
        ),
        named AS (
            SELECT h.*, coalesce(m.ticker, m.name) AS lender, m.is_public
            FROM h JOIN ref.bdc_master m USING (cik)
        )
        SELECT borrower_key, period_end, instrument_type,
               any_value(issuer_name) AS issuer_name, any_value(industry) AS industry,
               count(*) AS n_bdcs, count(*) FILTER (WHERE is_public) AS n_public,
               sum(cost) AS total_cost, sum(fv) / sum(cost) AS wavg_mark,
               min(mark) AS low_mark, max(mark) AS high_mark, max(mark) - min(mark) AS gap,
               arg_min(cik, mark) AS low_cik, arg_min(lender, mark) AS low_lender,
               arg_max(cik, mark) AS high_cik, arg_max(lender, mark) AS high_lender,
               bool_or(nonaccrual) AS any_nonaccrual, count(*) FILTER (WHERE nonaccrual) AS n_nonaccrual,
               string_agg(lender || ' ' || format('{{:.2f}}', mark), ', ' ORDER BY mark) AS lenders
        FROM named
        GROUP BY 1, 2, 3
        HAVING count(*) >= 2
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.stale_bdc AS
        WITH h AS (
            SELECT q.issuer_norm AS borrower_key, q.cik, q.period_end, q.instrument_type,
                   sum(q.fair_value) AS fv, sum(q.cost) AS cost,
                   q.issuer_norm <> '' AND length(q.issuer_norm) >= 4 AS matchable
            FROM signals.loan_quarter q
            WHERE q.is_debt AND q.data_ok AND q.cost > 0 AND q.fair_value IS NOT NULL
              AND q.fair_value / q.cost BETWEEN {MARK_LO} AND {MARK_HI}
            GROUP BY 1, 2, 3, 4, matchable
        ),
        unit AS (
            SELECT borrower_key, period_end, instrument_type,
                   count(*) AS n_bdcs, sum(fv) AS unit_fv, sum(cost) AS unit_cost
            FROM h WHERE matchable GROUP BY 1, 2, 3
        ),
        shared AS (
            -- the other lenders' cost-weighted mark on the same borrower and lien type
            SELECT h.cik, h.period_end, h.cost, h.fv,
                   (u.unit_fv - h.fv) / nullif(u.unit_cost - h.cost, 0) AS peer_mark,
                   h.fv / h.cost AS own_mark
            FROM h JOIN unit u USING (borrower_key, period_end, instrument_type)
            WHERE u.n_bdcs >= 2 AND h.matchable
        ),
        per_bdc AS (
            SELECT cik, period_end,
                   count(*) AS n_shared,
                   sum(cost) AS shared_cost,
                   sum(fv) / nullif(sum(cost), 0) AS own_mark_shared,
                   sum(peer_mark * cost) / nullif(sum(cost), 0) AS peer_mark_shared,
                   count(*) FILTER (WHERE own_mark - peer_mark > 0.05) AS n_above_5,
                   count(*) FILTER (WHERE own_mark - peer_mark < -0.05) AS n_below_5
            FROM shared GROUP BY 1, 2
        ),
        book AS (
            SELECT cik, period_end, sum(cost) AS debt_cost FROM h GROUP BY 1, 2
        )
        SELECT b.cik, b.period_end, b.debt_cost,
               coalesce(p.n_shared, 0) AS n_shared,
               coalesce(p.shared_cost, 0) AS shared_cost,
               coalesce(p.shared_cost, 0) / nullif(b.debt_cost, 0) AS shared_cost_share,
               1 - coalesce(p.shared_cost, 0) / nullif(b.debt_cost, 0) AS no_second_opinion_share,
               p.own_mark_shared, p.peer_mark_shared,
               p.own_mark_shared - p.peer_mark_shared AS generosity,
               coalesce(p.n_above_5, 0) AS n_above_5, coalesce(p.n_below_5, 0) AS n_below_5
        FROM book b LEFT JOIN per_bdc p USING (cik, period_end)
        """
    )
    _run_test(con, log)
    n_b, n_q = con.execute(
        "SELECT count(*), count(DISTINCT period_end) FROM signals.stale_borrowers"
    ).fetchone()
    n_bdc = con.execute("SELECT count(*) FROM signals.stale_bdc WHERE generosity IS NOT NULL").fetchone()[0]
    return f"stale marks: {n_b:,} shared borrower-type-quarters over {n_q} quarters; generosity for {n_bdc:,} BDC-quarters"


def _run_test(con: duckdb.DuckDBPyConnection, log) -> None:
    """Top fifth vs bottom fifth of public BDCs on generosity, by calendar quarter, against
    what happened to NAV per share and the share of debt below 90 over the next 1-2 quarters."""
    df = con.execute(
        f"""
        WITH u AS (
            SELECT s.cik, m.ticker, s.period_end, {QTR_SQL.format(col="s.period_end")} AS qtr,
                   s.generosity, s.n_shared, s.no_second_opinion_share,
                   b.pct_debt_below_90, f.nav_per_share
            FROM signals.stale_bdc s
            JOIN ref.bdc_master m USING (cik)
            JOIN signals.bdc_quarter b USING (cik, period_end)
            LEFT JOIN signals.bdc_fundamentals f USING (cik, period_end)
            WHERE m.is_public AND m.ticker IS NOT NULL AND b.data_ok AND b.n_debt >= 10
              AND s.generosity IS NOT NULL AND s.n_shared >= {MIN_SHARED}
        )
        SELECT u.*,
               n1.nav_per_share / nullif(u.nav_per_share, 0) - 1 AS nav_chg_fwd1,
               n2.nav_per_share / nullif(u.nav_per_share, 0) - 1 AS nav_chg_fwd2,
               b1.pct_debt_below_90 - u.pct_debt_below_90 AS b90_chg_fwd1,
               b2.pct_debt_below_90 - u.pct_debt_below_90 AS b90_chg_fwd2
        FROM u
        LEFT JOIN LATERAL (SELECT nav_per_share FROM signals.bdc_fundamentals x WHERE x.cik = u.cik
                             AND x.period_end BETWEEN u.period_end + INTERVAL 75 DAY AND u.period_end + INTERVAL 105 DAY
                           ORDER BY x.period_end LIMIT 1) n1 ON TRUE
        LEFT JOIN LATERAL (SELECT nav_per_share FROM signals.bdc_fundamentals x WHERE x.cik = u.cik
                             AND x.period_end BETWEEN u.period_end + INTERVAL 165 DAY AND u.period_end + INTERVAL 195 DAY
                           ORDER BY x.period_end LIMIT 1) n2 ON TRUE
        LEFT JOIN LATERAL (SELECT pct_debt_below_90 FROM signals.bdc_quarter x WHERE x.cik = u.cik AND x.data_ok
                             AND x.period_end BETWEEN u.period_end + INTERVAL 75 DAY AND u.period_end + INTERVAL 105 DAY
                           ORDER BY x.period_end LIMIT 1) b1 ON TRUE
        LEFT JOIN LATERAL (SELECT pct_debt_below_90 FROM signals.bdc_quarter x WHERE x.cik = u.cik AND x.data_ok
                             AND x.period_end BETWEEN u.period_end + INTERVAL 165 DAY AND u.period_end + INTERVAL 195 DAY
                           ORDER BY x.period_end LIMIT 1) b2 ON TRUE
        ORDER BY qtr, ticker
        """
    ).pl()
    con.execute("CREATE OR REPLACE TABLE signals.stale_test_universe AS SELECT * FROM df")

    period_rows: list[tuple] = []
    for qtr in df["qtr"].unique(maintain_order=True).to_list():
        g = df.filter(df["qtr"] == qtr)
        for name, (col, _) in OUTCOMES.items():
            pairs = sorted(
                (s, y, t, b) for s, y, t, b in zip(g["generosity"].to_list(), g[col].to_list(),
                                                   g["ticker"].to_list(), g["pct_debt_below_90"].to_list())
                if s is not None and y is not None
            )
            n = len(pairs)
            if n < MIN_NAMES:
                continue
            k = max(2, n // 5)
            bottom, top = pairs[:k], pairs[-k:]
            top_mean = sum(y for _, y, _, _ in top) / k
            bottom_mean = sum(y for _, y, _, _ in bottom) / k
            ic = _spearman([s for s, _, _, _ in pairs], [y for _, y, _, _ in pairs])
            b90 = lambda grp: sum(b for _, _, _, b in grp if b is not None) / max(1, sum(1 for _, _, _, b in grp if b is not None))
            period_rows.append((qtr, name, n, k, top_mean, bottom_mean, top_mean - bottom_mean, ic,
                                b90(top), b90(bottom),
                                ",".join(t for _, _, t, _ in reversed(top)), ",".join(t for _, _, t, _ in bottom)))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.stale_test_periods (
            qtr DATE, outcome VARCHAR, n INTEGER, k INTEGER, top_mean DOUBLE, bottom_mean DOUBLE,
            spread DOUBLE, ic DOUBLE, top_b90_now DOUBLE, bottom_b90_now DOUBLE,
            top_names VARCHAR, bottom_names VARCHAR
        )
        """
    )
    con.executemany("INSERT INTO signals.stale_test_periods VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", period_rows)

    summary: list[tuple] = []
    for name, (_, sign) in OUTCOMES.items():
        per = [r for r in period_rows if r[1] == name]
        if not per:
            continue
        spreads = [r[6] for r in per]
        ics = [r[7] for r in per if r[7] is not None]
        hit = sum(1 for s in spreads if sign * s > 0) / len(spreads)
        summary.append((name, "lower" if sign < 0 else "higher", len(per), sum(spreads) / len(spreads),
                        _tstat(spreads), hit, sum(ics) / len(ics) if ics else None, _tstat(ics) if ics else None,
                        sum(1 for i in ics if sign * i > 0) / len(ics) if ics else None))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.stale_test_summary (
            outcome VARCHAR, expected_for_generous VARCHAR, n_quarters INTEGER, mean_spread DOUBLE,
            spread_tstat DOUBLE, hit_rate DOUBLE, mean_ic DOUBLE, ic_tstat DOUBLE, ic_hit_rate DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.stale_test_summary VALUES (?,?,?,?,?,?,?,?,?)", summary)
    for r in summary:
        log(f"  stale test {r[0]}: {r[2]} quarters, top-bottom {r[3]:+.4f} (t {r[4] if r[4] is None else round(r[4], 2)}), "
            f"hit {r[5]:.0%}, IC {r[6] if r[6] is None else round(r[6], 3)}")
