"""Stale-mark detector: where lenders disagree about the same loan, and whether a BDC that
marks its shared borrowers above the other lenders pays for it later.

Unit of comparison: one borrower, one lien type (first lien, second lien, ...), one quarter,
held by two or more BDCs under different managers (sister vehicles of one sponsor share a
valuation committee and are folded into one opinion) whose quarter passed the reconciliation gate. Each lender's position
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


# One valuation committee per manager: vehicles of the same sponsor do not give independent
# opinions. (regex on the lower-cased name, manager key). Anything unmatched keys on the first
# word of its name, which groups "ARES CAPITAL CORP" with "ARES STRATEGIC INCOME FUND".
MANAGER_ALIASES: list[tuple[str, str]] = [
    (r"goldman|phillip street|west bay", "goldman"),
    (r"morgan stanley|north haven|^t series|^sl investment|^lgam", "morgan stanley"),
    (r"blue owl|owl rock", "blue owl"),
    (r"^fs |kkr", "fs kkr"),
    (r"main street|^msc income", "main street"),
    (r"apollo|midcap financial", "apollo"),
    (r"nuveen|churchill|^nc private", "churchill"),
    (r"kayne", "kayne"),
    (r"bc partners|^bcp ", "bc partners"),
    (r"franklin bsp|^fblc|benefit street", "franklin bsp"),
    (r"t\. rowe|^oha ", "oha"),
    (r"blackstone|^bxsl", "blackstone"),
    (r"golub", "golub"),
    (r"carlyle", "carlyle"),
    (r"new mountain", "new mountain"),
    (r"bain capital", "bain"),
    (r"oaktree", "oaktree"),
    (r"sixth street", "sixth street"),
    (r"pennantpark", "pennantpark"),
    (r"barings", "barings"),
    (r"crescent", "crescent"),
    (r"triplepoint", "triplepoint"),
    (r"hercules", "hercules"),
    (r"palmer square", "palmer square"),
    (r"stellus", "stellus"),
    (r"monroe", "monroe"),
    (r"antares", "antares"),
    (r"hps ", "hps"),
    (r"ares ", "ares"),
    (r"gladstone", "gladstone"),
    (r"prospect", "prospect"),
    (r"saratoga", "saratoga"),
    (r"fidelity", "fidelity"),
    (r"first eagle", "first eagle"),
    (r"lord abbett", "lord abbett"),
    (r"kennedy lewis", "kennedy lewis"),
    (r"vista credit", "vista"),
    (r"jefferies", "jefferies"),
    (r"diameter", "diameter"),
    (r"stone point", "stone point"),
    (r"tcw", "tcw"),
    (r"overland", "overland"),
    (r"north haven", "morgan stanley"),
]


def _manager_sql() -> str:
    cases = "\n".join(f"WHEN regexp_matches(lower(name), '{pat}') THEN '{key}'" for pat, key in MANAGER_ALIASES)
    return f"CASE {cases} ELSE split_part(lower(regexp_replace(name, '^the ', '', 'i')), ' ', 1) END"


def build_stale_marks(con: duckdb.DuckDBPyConnection, log=print) -> str:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.bdc_manager AS
        SELECT cik, name, ticker, {_manager_sql()} AS manager FROM ref.bdc_master
        """
    )
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
            SELECT h.*, coalesce(m.ticker, m.name) AS lender, m.is_public, mg.manager
            FROM h JOIN ref.bdc_master m USING (cik) JOIN signals.bdc_manager mg USING (cik)
        ),
        -- one opinion per manager: vehicles of the same sponsor share a valuation committee
        by_mgr AS (
            SELECT borrower_key, period_end, instrument_type, manager,
                   sum(fv) / sum(cost) AS mark, sum(cost) AS cost,
                   arg_min(cik, mark) AS low_cik, arg_min(lender, mark) AS low_lender,
                   arg_max(cik, mark) AS high_cik, arg_max(lender, mark) AS high_lender
            FROM named GROUP BY 1, 2, 3, 4
        ),
        mgr_span AS (
            SELECT borrower_key, period_end, instrument_type,
                   count(*) AS n_managers,
                   min(mark) AS low_mark, max(mark) AS high_mark,
                   arg_min(low_cik, mark) AS low_cik, arg_min(low_lender, mark) AS low_lender,
                   arg_max(high_cik, mark) AS high_cik, arg_max(high_lender, mark) AS high_lender
            FROM by_mgr GROUP BY 1, 2, 3
        )
        SELECT n.borrower_key, n.period_end, n.instrument_type,
               any_value(n.issuer_name) AS issuer_name, any_value(n.industry) AS industry,
               count(*) AS n_bdcs, count(*) FILTER (WHERE n.is_public) AS n_public,
               any_value(ms.n_managers) AS n_managers,
               sum(n.cost) AS total_cost, sum(n.fv) / sum(n.cost) AS wavg_mark,
               any_value(ms.low_mark) AS low_mark, any_value(ms.high_mark) AS high_mark,
               any_value(ms.high_mark - ms.low_mark) AS gap,
               any_value(ms.low_cik) AS low_cik, any_value(ms.low_lender) AS low_lender,
               any_value(ms.high_cik) AS high_cik, any_value(ms.high_lender) AS high_lender,
               bool_or(n.nonaccrual) AS any_nonaccrual, count(*) FILTER (WHERE n.nonaccrual) AS n_nonaccrual,
               string_agg(n.lender || ' ' || format('{{:.2f}}', n.mark), ', ' ORDER BY n.mark) AS lenders
        FROM named n JOIN mgr_span ms USING (borrower_key, period_end, instrument_type)
        GROUP BY 1, 2, 3
        HAVING count(*) >= 2 AND any_value(ms.n_managers) >= 2
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.stale_bdc AS
        WITH h AS (
            SELECT q.issuer_norm AS borrower_key, q.cik, q.period_end, q.instrument_type, mg.manager,
                   sum(q.fair_value) AS fv, sum(q.cost) AS cost,
                   q.issuer_norm <> '' AND length(q.issuer_norm) >= 4 AS matchable
            FROM signals.loan_quarter q JOIN signals.bdc_manager mg USING (cik)
            WHERE q.is_debt AND q.data_ok AND q.cost > 0 AND q.fair_value IS NOT NULL
              AND q.fair_value / q.cost BETWEEN {MARK_LO} AND {MARK_HI}
            GROUP BY 1, 2, 3, 4, 5, matchable
        ),
        unit AS (
            SELECT borrower_key, period_end, instrument_type,
                   count(DISTINCT manager) AS n_managers, sum(fv) AS unit_fv, sum(cost) AS unit_cost
            FROM h WHERE matchable GROUP BY 1, 2, 3
        ),
        own_mgr AS (
            SELECT borrower_key, period_end, instrument_type, manager,
                   sum(fv) AS mgr_fv, sum(cost) AS mgr_cost
            FROM h WHERE matchable GROUP BY 1, 2, 3, 4
        ),
        shared AS (
            -- the OTHER MANAGERS' cost-weighted mark on the same borrower and lien type; sister
            -- vehicles of the same sponsor are not a second opinion
            SELECT h.cik, h.period_end, h.cost, h.fv,
                   (u.unit_fv - o.mgr_fv) / nullif(u.unit_cost - o.mgr_cost, 0) AS peer_mark,
                   h.fv / h.cost AS own_mark
            FROM h
            JOIN unit u USING (borrower_key, period_end, instrument_type)
            JOIN own_mgr o USING (borrower_key, period_end, instrument_type, manager)
            WHERE u.n_managers >= 2 AND h.matchable
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
