"""Second-layer analytics: a validated loan risk score, borrower / sector / vintage rollups,
a backtest of the BDC-level signals, lender scorecards and watchlists.

Everything is fitted on our own history (2022-2026): a signal's weight is the log of how much
more often loans carrying it went bad (non-accrual, marked below 80, or exited at a loss)
within the next four quarters than loans in general.
"""
from __future__ import annotations

import math

import duckdb
import polars as pl

# binary loan-quarter features used by the risk model, with the human reason attached
FEATURES: list[tuple[str, str, str]] = [
    # (name, SQL predicate over signals.loan_quarter (alias q) + peer columns, reason text)
    ("mark_lt_80", "q.mark < 0.80", "marked below 80"),
    ("mark_80_90", "q.mark >= 0.80 AND q.mark < 0.90", "marked 80 to 90"),
    ("mark_90_95", "q.mark >= 0.90 AND q.mark < 0.95", "marked 90 to 95"),
    ("mark_95_98", "q.mark >= 0.95 AND q.mark < 0.98", "marked 95 to 98"),
    ("drop_1q_gt5", "q.mark_chg < -0.05", "marked down more than 5 points this quarter"),
    ("drop_1q_2_5", "q.mark_chg <= -0.02 AND q.mark_chg >= -0.05", "marked down 2 to 5 points this quarter"),
    ("drop_2q_gt5", "q.mark_chg_2q < -0.05", "down more than 5 points over two quarters"),
    ("nonaccrual", "q.nonaccrual_flag", "on non-accrual"),
    ("new_nonaccrual", "q.new_nonaccrual", "placed on non-accrual this quarter"),
    ("pik", "q.pik_flag", "paying in kind"),
    ("new_pik", "q.new_pik", "switched to PIK this quarter"),
    ("spread_up", "q.spread_up", "spread increased (amendment)"),
    ("extended", "q.maturity_extended", "maturity extended"),
    ("peer_nonaccrual", "q.peer_nonaccrual AND NOT q.nonaccrual_flag", "another lender has this borrower on non-accrual"),
    ("marked_above_peers", "q.mark_vs_peers > 0.03", "marked above other lenders"),
    ("peer_min_lt_90", "q.peer_min_mark < 0.90 AND q.mark >= 0.95", "another lender marks this borrower below 90"),
]

BAD_WINDOW_DAYS = 380  # four quarters, with slack for irregular period ends


def _feature_frame(con: duckdb.DuckDBPyConnection) -> None:
    """signals.loan_features: one row per debt loan-quarter with binary features and the
    forward outcome (bad within four quarters), NULL when the window is censored."""
    feats = ",\n".join(f"coalesce({pred}, FALSE) AS {name}" for name, pred, _ in FEATURES)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.loan_features AS
        WITH peers AS (
            SELECT b.cik, b.period_end, b.borrower_key, b.instrument_type,
                   max(b.mark_vs_peers) AS mark_vs_peers, bool_or(b.any_nonaccrual) AS peer_nonaccrual,
                   min(b.min_mark) AS peer_min_mark
            FROM signals.borrower_marks b GROUP BY 1, 2, 3, 4
        ),
        base AS (
            SELECT lq.loan_id, lq.cik, lq.period_end, lq.issuer_norm, lq.instrument_type,
                   lq.mark, lq.mark_chg,
                   lq.mark - lag(lq.mark, 2) OVER (PARTITION BY lq.loan_id ORDER BY lq.period_end) AS mark_chg_2q,
                   lq.nonaccrual_flag, lq.new_nonaccrual, lq.pik_flag, lq.new_pik, lq.spread_up,
                   lq.maturity_extended, lq.cost, lq.fair_value, lq.data_ok,
                   p.mark_vs_peers, p.peer_nonaccrual, p.peer_min_mark
            FROM signals.loan_quarter lq
            LEFT JOIN peers p ON p.cik = lq.cik AND p.period_end = lq.period_end
                 AND p.borrower_key = lq.issuer_norm AND p.instrument_type = lq.instrument_type
            WHERE lq.is_debt
        ),
        latest AS (SELECT cik, max(period_end) AS bdc_latest FROM core.holdings GROUP BY cik),
        fut AS (
            SELECT a.loan_id, a.period_end,
                   bool_or(b.nonaccrual_flag OR b.mark < 0.80) AS bad_obs
            FROM base a JOIN base b ON b.loan_id = a.loan_id
                 AND b.period_end > a.period_end AND b.period_end <= a.period_end + INTERVAL {BAD_WINDOW_DAYS} DAY
            GROUP BY 1, 2
        )
        SELECT q.*, {feats},
               CASE
                   WHEN q.nonaccrual_flag OR q.mark < 0.80 THEN NULL  -- already bad: not a prediction
                   WHEN q.period_end > l.bdc_latest - INTERVAL {BAD_WINDOW_DAYS} DAY
                        AND NOT coalesce(f.bad_obs, FALSE)
                        AND NOT (ln.exit_type = 'loss' AND ln.last_period <= q.period_end + INTERVAL {BAD_WINDOW_DAYS} DAY)
                       THEN NULL  -- window not fully observed yet
                   ELSE coalesce(f.bad_obs, FALSE)
                        OR (ln.exit_type = 'loss' AND ln.last_period > q.period_end
                            AND ln.last_period <= q.period_end + INTERVAL {BAD_WINDOW_DAYS} DAY)
               END AS bad_4q
        FROM base q
        LEFT JOIN fut f ON f.loan_id = q.loan_id AND f.period_end = q.period_end
        JOIN core.loans ln ON ln.loan_id = q.loan_id
        JOIN latest l ON l.cik = q.cik
        """
    )


def _fit(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Log-lift weight per feature from the observed outcomes; also writes
    signals.signal_validation for display."""
    base = con.execute(
        "SELECT avg(bad_4q::INT), count(*) FROM signals.loan_features WHERE bad_4q IS NOT NULL AND data_ok"
    ).fetchone()
    base_rate, n_all = float(base[0] or 0.0), int(base[1] or 0)
    rows = []
    weights: dict[str, float] = {}
    for name, _, reason in FEATURES:
        r = con.execute(
            f"""
            SELECT count(*), avg(bad_4q::INT)
            FROM signals.loan_features WHERE bad_4q IS NOT NULL AND data_ok AND {name}
            """
        ).fetchone()
        n, hit = int(r[0] or 0), float(r[1] or 0.0)
        lift = (hit / base_rate) if base_rate > 0 and n >= 30 else 1.0
        w = max(0.0, min(3.0, math.log(lift))) if lift > 0 else 0.0
        weights[name] = w
        rows.append((name, reason, n, hit, base_rate, lift, w))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.signal_validation (
            feature VARCHAR, reason VARCHAR, n_loan_quarters INTEGER, bad_rate DOUBLE,
            base_rate DOUBLE, lift DOUBLE, weight DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.signal_validation VALUES (?,?,?,?,?,?,?)", rows)
    con.execute(
        "CREATE OR REPLACE TABLE signals.model_meta AS SELECT ? AS base_rate, ? AS n_loan_quarters, "
        "current_date AS fitted_on",
        [base_rate, n_all],
    )
    return weights


def _score(con: duckdb.DuckDBPyConnection, weights: dict[str, float]) -> None:
    """signals.loan_risk: probability-like score 0-100 and the reasons behind it."""
    base_rate = con.execute("SELECT base_rate FROM signals.model_meta").fetchone()[0] or 0.05
    base_logit = math.log(base_rate / (1 - base_rate))
    terms = " + ".join(f"CASE WHEN {n} THEN {w:.4f} ELSE 0 END" for n, w in weights.items() if w > 0)
    reasons = ", ".join(
        f"CASE WHEN {n} THEN '{r.replace(chr(39), '')}' END" for n, _, r in FEATURES if weights.get(n, 0) > 0
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.loan_risk AS
        SELECT loan_id, cik, period_end, issuer_norm, instrument_type, cost, fair_value, mark,
               nonaccrual_flag, data_ok,
               CASE WHEN nonaccrual_flag OR mark < 0.80 THEN 100
                    ELSE round(100 / (1 + exp(-({base_logit:.4f} + {terms or '0'})))) END AS risk_score,
               list_filter([{reasons}], x -> x IS NOT NULL) AS reasons,
               row_number() OVER (PARTITION BY loan_id ORDER BY period_end DESC) = 1 AS is_latest
        FROM signals.loan_features
        """
    )


def _rollups(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.borrower_quarter AS
        SELECT r.issuer_norm AS borrower_key, r.period_end,
               any_value(l.issuer_name) AS issuer_name, any_value(l.industry) AS industry,
               count(DISTINCT r.cik) AS n_bdcs, count(*) AS n_loans,
               sum(r.cost) AS cost, sum(r.fair_value) AS fair_value,
               sum(r.fair_value) / nullif(sum(r.cost), 0) AS mark,
               min(r.mark) AS min_mark, max(r.mark) AS max_mark,
               bool_or(r.nonaccrual_flag) AS any_nonaccrual,
               max(r.risk_score) AS max_risk, sum(r.risk_score * r.cost) / nullif(sum(r.cost), 0) AS wavg_risk
        FROM signals.loan_risk r JOIN core.loans l USING (loan_id)
        WHERE r.data_ok AND r.issuer_norm <> '' AND r.cost > 0
        GROUP BY 1, 2
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.sector_quarter AS
        SELECT coalesce(nullif(h.industry, ''), 'Unknown') AS industry, h.period_end,
               count(*) AS n_loans, count(DISTINCT h.cik) AS n_bdcs, sum(h.cost) AS cost,
               sum(h.fair_value) / nullif(sum(h.cost), 0) AS mark,
               sum(h.cost) FILTER (WHERE h.mark < 0.95) / nullif(sum(h.cost), 0) AS pct_stressed,
               sum(h.cost) FILTER (WHERE h.nonaccrual_flag) / nullif(sum(h.cost), 0) AS pct_nonaccrual,
               sum(r.risk_score * h.cost) / nullif(sum(h.cost), 0) AS wavg_risk
        FROM signals.loan_quarter h JOIN signals.loan_risk r ON r.loan_id = h.loan_id AND r.period_end = h.period_end
        WHERE h.is_debt AND h.data_ok AND h.cost > 0
        GROUP BY 1, 2
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.vintage_quarter AS
        SELECT year(l.first_period) AS vintage, h.period_end,
               count(*) AS n_loans, sum(h.cost) AS cost,
               sum(h.fair_value) / nullif(sum(h.cost), 0) AS mark,
               sum(h.cost) FILTER (WHERE h.mark < 0.95) / nullif(sum(h.cost), 0) AS pct_stressed,
               sum(h.cost) FILTER (WHERE h.nonaccrual_flag) / nullif(sum(h.cost), 0) AS pct_nonaccrual
        FROM signals.loan_quarter h JOIN core.loans l USING (loan_id)
        WHERE h.is_debt AND h.data_ok AND h.cost > 0
        GROUP BY 1, 2
        """
    )


BDC_COMPONENTS = [
    "quality_score", "pct_debt_below_90", "nonaccrual_pct_cost", "pik_share",
    "new_deterioration_rate", "new_nonaccrual_rate", "d4_pct_debt_below_90",
    "d4_nonaccrual_pct_cost", "d1_debt_mark", "markdown_share", "generosity", "wavg_risk",
]


def _backtest(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Does each BDC-level signal predict the next four quarters' NAV change and stock return?
    Writes signals.bdc_backtest and returns weights (positive = predicts NAV declines)."""
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bdc_forward AS
        WITH q AS (
            SELECT b.cik, b.period_end, b.quality_score, b.pct_debt_below_90, b.nonaccrual_pct_cost,
                   b.pik_share, b.new_deterioration_rate, b.new_nonaccrual_rate, b.d4_pct_debt_below_90,
                   b.d4_nonaccrual_pct_cost, b.d1_debt_mark, b.markdown_share, g.generosity,
                   wr.wavg_risk, b.data_ok
            FROM signals.bdc_quarter b
            LEFT JOIN signals.bdc_generosity g ON g.cik = b.cik AND g.period_end = b.period_end
            LEFT JOIN (
                SELECT cik, period_end, sum(risk_score * cost) / nullif(sum(cost), 0) AS wavg_risk
                FROM signals.loan_risk WHERE data_ok GROUP BY 1, 2
            ) wr ON wr.cik = b.cik AND wr.period_end = b.period_end
        ),
        nav AS (SELECT cik, period_end, nav_per_share FROM market.nav WHERE nav_per_share > 0),
        px AS (SELECT cik, period_end, price_at_period_end FROM market.pnav_history)
        SELECT q.*, m.ticker,
               n1.nav_per_share AS nav_now, n2.nav_per_share AS nav_4q,
               n2.nav_per_share / n1.nav_per_share - 1 AS fwd_nav_chg_4q,
               p2.price_at_period_end / nullif(p1.price_at_period_end, 0) - 1 AS fwd_price_chg_4q
        FROM q JOIN ref.bdc_master m USING (cik)
        LEFT JOIN nav n1 ON n1.cik = q.cik AND n1.period_end = q.period_end
        LEFT JOIN nav n2 ON n2.cik = q.cik AND n2.period_end BETWEEN q.period_end + INTERVAL 350 DAY AND q.period_end + INTERVAL 380 DAY
        LEFT JOIN px p1 ON p1.cik = q.cik AND p1.period_end = q.period_end
        LEFT JOIN px p2 ON p2.cik = q.cik AND p2.period_end = n2.period_end
        WHERE m.is_public AND q.data_ok
        """
    )
    df = con.execute("SELECT * FROM signals.bdc_forward WHERE fwd_nav_chg_4q IS NOT NULL").pl()
    rows, weights = [], {}
    for c in BDC_COMPONENTS:
        d = df.select([c, "fwd_nav_chg_4q", "fwd_price_chg_4q"]).drop_nulls(subset=[c, "fwd_nav_chg_4q"])
        n = d.height
        if n < 30:
            rows.append((c, n, None, None, None, 0.0))
            weights[c] = 0.0
            continue
        r_nav = d.select(pl.corr(pl.col(c).rank(), pl.col("fwd_nav_chg_4q").rank())).item()
        dp = d.drop_nulls(subset=["fwd_price_chg_4q"])
        r_px = dp.select(pl.corr(pl.col(c).rank(), pl.col("fwd_price_chg_4q").rank())).item() if dp.height >= 30 else None
        # top vs bottom quintile forward NAV change
        qs = d.with_columns((pl.col(c).rank() / n).alias("pr"))
        top = qs.filter(pl.col("pr") > 0.8)["fwd_nav_chg_4q"].mean()
        bot = qs.filter(pl.col("pr") <= 0.2)["fwd_nav_chg_4q"].mean()
        spread = (top - bot) if top is not None and bot is not None else None
        w = max(0.0, -(r_nav or 0.0))  # higher signal should mean lower future NAV
        weights[c] = w
        rows.append((c, n, r_nav, r_px, spread, w))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bdc_backtest (
            component VARCHAR, n_bdc_quarters INTEGER, spearman_fwd_nav DOUBLE,
            spearman_fwd_price DOUBLE, top_minus_bottom_quintile_nav DOUBLE, weight DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.bdc_backtest VALUES (?,?,?,?,?,?)", rows)
    return weights


def _validated_bdc_score(con: duckdb.DuckDBPyConnection, weights: dict[str, float]) -> None:
    """signals.bdc_validated: cross-sectional z-scores of the components weighted by their
    backtested predictive power (components that did not predict get zero weight)."""
    comps = [c for c, w in weights.items() if w > 0 and c != "quality_score"]
    if not comps:
        comps = ["pct_debt_below_90", "nonaccrual_pct_cost"]
        weights = {c: 1.0 for c in comps}
    z = ",\n".join(
        f"({c} - avg({c}) OVER pw) / nullif(stddev_samp({c}) OVER pw, 0) AS z_{c}" for c in comps
    )
    num = " + ".join(f"{weights[c]:.4f} * CASE WHEN z_{c} IS NULL THEN 0 ELSE least(greatest(z_{c}, -3), 3) END" for c in comps)
    den = " + ".join(f"{weights[c]:.4f} * (z_{c} IS NOT NULL)::INT" for c in comps)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.bdc_validated AS
        WITH src AS (
            SELECT b.cik, b.period_end, b.data_ok, b.n_debt, b.pct_debt_below_90, b.nonaccrual_pct_cost,
                   b.pik_share, b.new_deterioration_rate, b.new_nonaccrual_rate, b.d4_pct_debt_below_90,
                   b.d4_nonaccrual_pct_cost, b.d1_debt_mark, b.markdown_share, g.generosity, wr.wavg_risk
            FROM signals.bdc_quarter b
            LEFT JOIN signals.bdc_generosity g ON g.cik = b.cik AND g.period_end = b.period_end
            LEFT JOIN (SELECT cik, period_end, sum(risk_score * cost) / nullif(sum(cost), 0) AS wavg_risk
                       FROM signals.loan_risk WHERE data_ok GROUP BY 1, 2) wr
                   ON wr.cik = b.cik AND wr.period_end = b.period_end
        ),
        z AS (SELECT src.*, {z} FROM src WINDOW pw AS (PARTITION BY period_end, data_ok))
        SELECT cik, period_end, wavg_risk,
               CASE WHEN data_ok AND n_debt >= 10 AND ({den}) > 0 THEN ({num}) / ({den}) END AS validated_score
        FROM z
        """
    )


def _scorecards(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.lender_scorecard AS
        WITH f AS (
            SELECT lf.cik, lf.loan_id, lf.period_end, lf.mark, lf.bad_4q, lf.cost
            FROM signals.loan_features lf WHERE lf.data_ok
        ),
        miss AS (
            -- loans carried at or above 97 that went bad within four quarters: marks were late
            SELECT cik, count(*) FILTER (WHERE mark >= 0.97 AND bad_4q) AS n_missed,
                   count(*) FILTER (WHERE mark >= 0.97 AND bad_4q IS NOT NULL) AS n_par_loans,
                   count(*) FILTER (WHERE bad_4q) AS n_bad,
                   count(*) FILTER (WHERE bad_4q IS NOT NULL) AS n_evaluated
            FROM f GROUP BY cik
        ),
        early AS (
            -- of loans that went on non-accrual, how many were already marked below 95 the quarter before
            SELECT q.cik, count(*) AS n_new_na,
                   count(*) FILTER (WHERE q.prev_mark < 0.95) AS n_warned
            FROM signals.loan_quarter q WHERE q.new_nonaccrual AND q.data_ok GROUP BY q.cik
        ),
        losses AS (
            SELECT cik, sum(last_cost) FILTER (WHERE exit_type = 'loss') AS loss_exit_cost,
                   count(*) FILTER (WHERE exit_type = 'loss') AS n_loss_exits
            FROM core.loans WHERE is_debt GROUP BY cik
        ),
        gen AS (
            SELECT cik, avg(generosity) AS generosity_4q, sum(n_shared) AS n_shared
            FROM (SELECT *, row_number() OVER (PARTITION BY cik ORDER BY period_end DESC) AS rn
                  FROM signals.bdc_generosity) WHERE rn <= 4 GROUP BY cik
        ),
        size AS (SELECT cik, avg(debt_cost) AS avg_debt_cost, max(period_end) AS latest FROM signals.bdc_quarter GROUP BY cik)
        SELECT m.cik, m.ticker, m.name, m.is_public, s.latest AS latest_period,
               miss.n_evaluated, miss.n_bad,
               CASE WHEN miss.n_evaluated >= 50 THEN miss.n_bad * 1.0 / miss.n_evaluated END AS bad_rate,
               CASE WHEN miss.n_par_loans >= 50 THEN miss.n_missed * 1.0 / miss.n_par_loans END AS late_mark_rate,
               early.n_new_na,
               CASE WHEN early.n_new_na >= 5 THEN early.n_warned * 1.0 / early.n_new_na END AS early_warning_rate,
               losses.n_loss_exits, losses.loss_exit_cost / nullif(s.avg_debt_cost, 0) AS loss_exit_rate,
               gen.generosity_4q, gen.n_shared
        FROM ref.bdc_master m
        JOIN size s USING (cik)
        LEFT JOIN miss USING (cik) LEFT JOIN early USING (cik) LEFT JOIN losses USING (cik) LEFT JOIN gen USING (cik)
        WHERE m.has_xbrl
        """
    )


def _watchlists(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.watchlist_loans AS
        WITH latest AS (
            SELECT r.*, l.issuer_name, l.industry, m.ticker, m.name AS bdc_name, m.is_public,
                   b.n_bdcs, b.avg_mark AS peer_avg_mark, b.mark_vs_peers
            FROM signals.loan_risk r
            JOIN core.loans l USING (loan_id)
            JOIN ref.bdc_master m ON m.cik = r.cik
            LEFT JOIN signals.borrower_marks b ON b.cik = r.cik AND b.period_end = r.period_end
                 AND b.borrower_key = r.issuer_norm AND b.instrument_type = r.instrument_type
            WHERE r.is_latest AND r.data_ok AND r.cost > 0
              AND r.period_end >= (SELECT max(period_end) FROM core.holdings) - INTERVAL 120 DAY
        )
        SELECT 'deteriorating' AS list, * FROM latest
        WHERE risk_score >= 40 AND NOT nonaccrual_flag AND mark >= 0.80
        UNION ALL
        SELECT 'marked_above_peers', * FROM latest WHERE n_bdcs >= 2 AND mark_vs_peers > 0.05
        UNION ALL
        SELECT 'marked_below_peers', * FROM latest WHERE n_bdcs >= 2 AND mark_vs_peers < -0.05
        UNION ALL
        SELECT 'single_lender_rich', * FROM latest
        WHERE coalesce(n_bdcs, 1) = 1 AND mark >= 0.99 AND risk_score >= 25
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.watchlist_stocks AS
        SELECT s.*, v.validated_score, v.wavg_risk, sc.late_mark_rate, sc.early_warning_rate,
               sc.loss_exit_rate,
               CASE
                 WHEN quadrant = 'short_candidate' THEN 'short'
                 WHEN quadrant = 'long_candidate' THEN 'long'
                 WHEN v.validated_score IS NOT NULL AND v.validated_score >= 0.75 AND p_nav >= p_nav_median THEN 'short'
                 WHEN v.validated_score IS NOT NULL AND v.validated_score <= -0.5 AND p_nav_pct <= 0.35 THEN 'long'
                 ELSE NULL END AS side,
               concat_ws('; ',
                 CASE WHEN quality_trend_4q > 0.25 THEN 'book deteriorating over 4 quarters' END,
                 CASE WHEN d4_nonaccrual_pct_cost > 0.01 THEN 'non-accruals up ' || round(100 * d4_nonaccrual_pct_cost, 1) || ' pts' END,
                 CASE WHEN d4_pct_debt_below_90 > 0.02 THEN 'debt below 90 up ' || round(100 * d4_pct_debt_below_90, 1) || ' pts' END,
                 CASE WHEN new_deterioration_rate > 0.03 THEN round(100 * new_deterioration_rate, 1) || '% of debt newly stressed' END,
                 CASE WHEN generosity > 0.02 THEN 'marks ' || round(100 * generosity, 1) || ' pts above other lenders' END,
                 CASE WHEN generosity < -0.02 THEN 'marks ' || round(-100 * generosity, 1) || ' pts below other lenders' END,
                 CASE WHEN sc.late_mark_rate > 0.05 THEN round(100 * sc.late_mark_rate, 0) || '% of par loans went bad within a year' END,
                 CASE WHEN p_nav >= 1.1 THEN 'trades at ' || round(p_nav, 2) || 'x NAV' END,
                 CASE WHEN p_nav <= 0.8 THEN 'trades at ' || round(p_nav, 2) || 'x NAV' END,
                 CASE WHEN quality_score < -0.5 AND coalesce(quality_trend_4q, 0) <= 0 THEN 'clean, stable book' END
               ) AS reasons
        FROM market.screen s
        LEFT JOIN signals.bdc_validated v ON v.cik = s.cik AND v.period_end = s.signal_period
        LEFT JOIN signals.lender_scorecard sc ON sc.cik = s.cik
        """
    )


def build_insights(con: duckdb.DuckDBPyConnection) -> str:
    _feature_frame(con)
    weights = _fit(con)
    _score(con, weights)
    _rollups(con)
    bw = _backtest(con)
    _validated_bdc_score(con, bw)
    _scorecards(con)
    _watchlists(con)
    meta = con.execute("SELECT base_rate, n_loan_quarters FROM signals.model_meta").fetchone()
    top = con.execute(
        "SELECT feature, round(lift,2) FROM signals.signal_validation ORDER BY lift DESC LIMIT 5"
    ).fetchall()
    wl = con.execute("SELECT list, count(*) FROM signals.watchlist_loans GROUP BY 1").fetchall()
    return (f"loan risk model: base 4q bad rate {meta[0]:.1%} over {meta[1]:,} loan-quarters; "
            f"strongest signals {top}; watchlist loans {wl}")
