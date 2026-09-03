"""Loan-quarter signals and BDC-quarter quality rollups."""
from __future__ import annotations

import duckdb

BUCKET_SQL = """
CASE WHEN mark IS NULL THEN NULL
     WHEN mark >= 0.98 THEN '1_ge98'
     WHEN mark >= 0.95 THEN '2_95_98'
     WHEN mark >= 0.90 THEN '3_90_95'
     WHEN mark >= 0.80 THEN '4_80_90'
     ELSE '5_lt80' END
"""

# z-scored components of the composite quality score (higher = worse book)
SCORE_COMPONENTS = [
    "pct_debt_below_90",
    "nonaccrual_pct_cost",
    "pik_share",
    "new_deterioration_rate",
    "new_nonaccrual_rate",
    "d4_pct_debt_below_90",
    "d4_nonaccrual_pct_cost",
    "d4_pik_share",
]


def build_loan_quarter(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.loan_quarter AS
        WITH h AS (
            SELECT lh.*, {BUCKET_SQL} AS mark_bucket,
                   r.coverage,
                   (r.coverage BETWEEN 0.85 AND 1.15) AS data_ok
            FROM core.loan_history lh
            LEFT JOIN core.reconciliation r ON r.cik = lh.cik AND r.period_end = lh.period_end
                 AND r.adsh = lh.adsh
        ),
        w AS (
            SELECT h.*,
                   lag(period_end) OVER lw AS prev_period,
                   lag(mark) OVER lw AS prev_mark,
                   lag(mark_bucket) OVER lw AS prev_bucket,
                   lag(nonaccrual_flag) OVER lw AS prev_nonaccrual,
                   lag(pik_flag) OVER lw AS prev_pik,
                   lag(pik_rate) OVER lw AS prev_pik_rate,
                   lag(spread) OVER lw AS prev_spread,
                   lag(rate) OVER lw AS prev_rate,
                   lag(maturity) OVER lw AS prev_maturity,
                   lag(cost) OVER lw AS prev_cost,
                   lag(fair_value) OVER lw AS prev_fair_value,
                   lag(instrument_type) OVER lw AS prev_instrument_type,
                   row_number() OVER lw AS obs_n
            FROM h
            WINDOW lw AS (PARTITION BY loan_id ORDER BY period_end)
        )
        SELECT *,
               mark - prev_mark AS mark_chg,
               (mark < 0.95 AND coalesce(prev_mark, 1) >= 0.95) AS crossed_below_95,
               (mark < 0.90 AND coalesce(prev_mark, 1) >= 0.90) AS crossed_below_90,
               (nonaccrual_flag AND NOT coalesce(prev_nonaccrual, FALSE)) AS new_nonaccrual,
               (pik_flag AND NOT coalesce(prev_pik, FALSE)) AS new_pik,
               (coalesce(pik_rate, 0) > coalesce(prev_pik_rate, 0) + 0.0001) AS pik_rate_up,
               (spread IS NOT NULL AND prev_spread IS NOT NULL AND spread > prev_spread + 0.0001)
                   AS spread_up,
               (maturity IS NOT NULL AND prev_maturity IS NOT NULL AND maturity > prev_maturity + INTERVAL 45 DAY)
                   AS maturity_extended,
               (prev_instrument_type IN ('first_lien','second_lien','subordinated','debt_other')
                    AND instrument_type IN ('equity','preferred','warrant','equity_other')) AS converted_to_equity,
               (mark IS NOT NULL AND mark < 0.95 AND is_debt) AS is_stressed,
               (obs_n = 1) AS is_new
        FROM w
        """
    )


def build_bdc_quarter(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bdc_quarter AS
        WITH base AS (
            SELECT cik, period_end,
                   any_value(data_ok) AS data_ok,
                   any_value(coverage) AS coverage,
                   count(*) AS n_holdings,
                   count(*) FILTER (WHERE is_debt) AS n_debt,
                   count(DISTINCT issuer_norm) AS n_issuers,
                   sum(fair_value) AS total_fv,
                   sum(cost) AS total_cost,
                   sum(fair_value) FILTER (WHERE is_debt) AS debt_fv,
                   sum(cost) FILTER (WHERE is_debt) AS debt_cost,
                   sum(cost) FILTER (WHERE is_debt AND mark < 0.95) AS debt_cost_below_95,
                   sum(cost) FILTER (WHERE is_debt AND mark < 0.90) AS debt_cost_below_90,
                   sum(cost) FILTER (WHERE is_debt AND mark < 0.80) AS debt_cost_below_80,
                   sum(cost) FILTER (WHERE nonaccrual_flag) AS nonaccrual_cost,
                   sum(fair_value) FILTER (WHERE nonaccrual_flag) AS nonaccrual_fv,
                   count(*) FILTER (WHERE nonaccrual_flag) AS n_nonaccrual,
                   sum(cost) FILTER (WHERE is_debt AND pik_flag) AS pik_cost,
                   sum(cost) FILTER (WHERE is_debt AND crossed_below_95) AS new_deterioration_cost,
                   sum(cost) FILTER (WHERE new_nonaccrual) AS new_nonaccrual_cost,
                   count(*) FILTER (WHERE new_nonaccrual) AS n_new_nonaccrual,
                   sum(cost) FILTER (WHERE is_debt AND spread_up) AS spread_up_cost,
                   sum(cost) FILTER (WHERE is_debt AND maturity_extended) AS extended_cost,
                   sum(cost) FILTER (WHERE converted_to_equity) AS converted_cost,
                   sum(cost) FILTER (WHERE is_debt AND mark_chg < -0.02) AS markdown_cost,
                   sum(cost) FILTER (WHERE is_debt AND mark_chg > 0.02) AS markup_cost,
                   sum(cost) FILTER (WHERE is_debt AND is_new) AS new_debt_cost,
                   sum(spread * cost) FILTER (WHERE is_debt AND spread IS NOT NULL)
                       / nullif(sum(cost) FILTER (WHERE is_debt AND spread IS NOT NULL), 0) AS wavg_spread,
                   sum(rate * cost) FILTER (WHERE is_debt AND rate IS NOT NULL)
                       / nullif(sum(cost) FILTER (WHERE is_debt AND rate IS NOT NULL), 0) AS wavg_rate,
                   count(*) FILTER (WHERE is_debt AND mark IS NOT NULL) AS n_debt_marked,
                   count(*) FILTER (WHERE is_debt AND mark < 0.95) AS n_debt_below_95
            FROM signals.loan_quarter
            GROUP BY cik, period_end
        ),
        exits AS (
            -- loans whose last observation was the BDC's previous period, attributed to the
            -- period in which they disappeared
            SELECT l.cik, nxt.period_end,
                   sum(l.last_cost) FILTER (WHERE l.last_mark < 0.9) AS exit_loss_cost,
                   sum(l.last_cost) AS exit_cost
            FROM core.loans l
            JOIN (
                SELECT cik, period_end,
                       lag(period_end) OVER (PARTITION BY cik ORDER BY period_end) AS prev_period
                FROM (SELECT DISTINCT cik, period_end FROM core.holdings)
            ) nxt ON nxt.cik = l.cik AND nxt.prev_period = l.last_period
            WHERE l.is_debt
            GROUP BY 1, 2
        ),
        m AS (
            SELECT b.*, e.exit_loss_cost, e.exit_cost,
                   debt_cost_below_95 / nullif(debt_cost, 0) AS pct_debt_below_95,
                   debt_cost_below_90 / nullif(debt_cost, 0) AS pct_debt_below_90,
                   debt_cost_below_80 / nullif(debt_cost, 0) AS pct_debt_below_80,
                   nonaccrual_cost / nullif(debt_cost, 0) AS nonaccrual_pct_cost,
                   nonaccrual_fv / nullif(debt_fv, 0) AS nonaccrual_pct_fv,
                   pik_cost / nullif(debt_cost, 0) AS pik_share,
                   new_deterioration_cost / nullif(debt_cost, 0) AS new_deterioration_rate,
                   new_nonaccrual_cost / nullif(debt_cost, 0) AS new_nonaccrual_rate,
                   spread_up_cost / nullif(debt_cost, 0) AS spread_up_share,
                   extended_cost / nullif(debt_cost, 0) AS extended_share,
                   converted_cost / nullif(debt_cost, 0) AS converted_share,
                   markdown_cost / nullif(debt_cost, 0) AS markdown_share,
                   markup_cost / nullif(debt_cost, 0) AS markup_share,
                   e.exit_loss_cost / nullif(debt_cost, 0) AS exit_loss_rate,
                   debt_fv / nullif(debt_cost, 0) AS debt_mark
            FROM base b LEFT JOIN exits e USING (cik, period_end)
        ),
        d AS (
            SELECT m.*,
                   pct_debt_below_90 - lag(pct_debt_below_90, 1) OVER w AS d1_pct_debt_below_90,
                   pct_debt_below_90 - lag(pct_debt_below_90, 4) OVER w AS d4_pct_debt_below_90,
                   nonaccrual_pct_cost - lag(nonaccrual_pct_cost, 1) OVER w AS d1_nonaccrual_pct_cost,
                   nonaccrual_pct_cost - lag(nonaccrual_pct_cost, 4) OVER w AS d4_nonaccrual_pct_cost,
                   pik_share - lag(pik_share, 1) OVER w AS d1_pik_share,
                   pik_share - lag(pik_share, 4) OVER w AS d4_pik_share,
                   debt_mark - lag(debt_mark, 1) OVER w AS d1_debt_mark,
                   debt_mark - lag(debt_mark, 4) OVER w AS d4_debt_mark,
                   row_number() OVER (PARTITION BY cik ORDER BY period_end DESC) AS periods_ago
            FROM m
            WINDOW w AS (PARTITION BY cik ORDER BY period_end)
        )
        SELECT * FROM d
        """
    )

    # composite quality score: mean z-score across BDCs with usable data in the same period
    zcols = ",\n".join(
        f"({c} - avg({c}) OVER pw) / nullif(stddev_samp({c}) OVER pw, 0) AS z_{c}"
        for c in SCORE_COMPONENTS
    )
    zmean = " + ".join(
        f"CASE WHEN z_{c} IS NULL THEN 0 ELSE least(greatest(z_{c}, -3), 3) END" for c in SCORE_COMPONENTS
    )
    zcount = " + ".join(f"(z_{c} IS NOT NULL)::INT" for c in SCORE_COMPONENTS)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.bdc_quarter AS
        WITH z AS (
            SELECT q.*, {zcols}
            FROM signals.bdc_quarter q
            WINDOW pw AS (PARTITION BY period_end, data_ok)
        ),
        s AS (
            SELECT z.*,
                   CASE WHEN data_ok AND n_debt >= 10 AND ({zcount}) >= 3
                             AND pct_debt_below_90 IS NOT NULL AND debt_mark IS NOT NULL
                        THEN ({zmean}) / ({zcount}) END AS quality_score
            FROM z
        )
        SELECT s.*,
               quality_score - lag(quality_score, 4) OVER w AS quality_trend_4q,
               quality_score - lag(quality_score, 1) OVER w AS quality_trend_1q
        FROM s
        WINDOW w AS (PARTITION BY cik ORDER BY period_end)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bdc_latest AS
        SELECT q.*, m.name, m.ticker, m.is_public
        FROM signals.bdc_quarter q JOIN ref.bdc_master m USING (cik)
        WHERE periods_ago = 1
        """
    )


def build_migration(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.migration AS
        SELECT cik, period_end, prev_bucket AS from_bucket, mark_bucket AS to_bucket,
               count(*) AS n, sum(cost) AS cost
        FROM signals.loan_quarter
        WHERE is_debt AND prev_bucket IS NOT NULL AND mark_bucket IS NOT NULL
        GROUP BY 1, 2, 3, 4
        """
    )


def build_borrower_marks(con: duckdb.DuckDBPyConnection) -> None:
    """Same borrower held by several BDCs: compare marks across lenders."""
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.borrower_marks AS
        WITH h AS (
            SELECT issuer_norm AS borrower_key, cik, period_end, instrument_type,
                   sum(fair_value) AS fv, sum(cost) AS cost,
                   sum(fair_value) / nullif(sum(cost), 0) AS mark,
                   bool_or(nonaccrual_flag) AS nonaccrual
            FROM signals.loan_quarter
            WHERE is_debt AND data_ok AND issuer_norm <> '' AND length(issuer_norm) >= 4
                  AND cost > 0
            GROUP BY 1, 2, 3, 4
        ),
        peers AS (
            SELECT borrower_key, period_end, instrument_type,
                   count(DISTINCT cik) AS n_bdcs,
                   avg(mark) AS avg_mark, min(mark) AS min_mark, max(mark) AS max_mark,
                   bool_or(nonaccrual) AS any_nonaccrual
            FROM h GROUP BY 1, 2, 3
        )
        SELECT h.*, p.n_bdcs, p.avg_mark, p.min_mark, p.max_mark, p.any_nonaccrual,
               h.mark - (p.avg_mark * p.n_bdcs - h.mark) / nullif(p.n_bdcs - 1, 0) AS mark_vs_peers
        FROM h JOIN peers p USING (borrower_key, period_end, instrument_type)
        WHERE p.n_bdcs >= 2
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bdc_generosity AS
        SELECT cik, period_end, count(*) AS n_shared,
               sum(mark_vs_peers * cost) / nullif(sum(cost), 0) AS generosity,
               count(*) FILTER (WHERE mark_vs_peers > 0.03) AS n_marked_above,
               count(*) FILTER (WHERE mark_vs_peers < -0.03) AS n_marked_below,
               count(*) FILTER (WHERE any_nonaccrual AND NOT nonaccrual) AS n_peer_nonaccrual_not_flagged
        FROM signals.borrower_marks
        GROUP BY 1, 2
        """
    )


def build_signals(con: duckdb.DuckDBPyConnection) -> str:
    build_loan_quarter(con)
    build_bdc_quarter(con)
    build_migration(con)
    build_borrower_marks(con)
    n = con.execute("SELECT count(*) FROM signals.bdc_quarter").fetchone()[0]
    ok = con.execute(
        "SELECT count(*) FROM signals.bdc_latest WHERE quality_score IS NOT NULL"
    ).fetchone()[0]
    return f"signals.bdc_quarter: {n} BDC-periods; {ok} BDCs with a current quality score"
