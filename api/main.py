"""FastAPI app exposing the DuckDB tables to the web UI."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from api import db

app = FastAPI(title="BDC Loan Quality API")

SCREEN_COLS = """
    cik, ticker, name, signal_period, data_ok, coverage, n_holdings, n_debt, debt_cost, debt_fv,
    debt_mark, pct_debt_below_95, pct_debt_below_90, pct_debt_below_80, nonaccrual_pct_cost,
    nonaccrual_pct_fv, n_nonaccrual, pik_share, new_deterioration_rate, new_nonaccrual_rate,
    markdown_share, markup_share, exit_loss_rate, wavg_spread, wavg_rate, d1_pct_debt_below_90,
    d4_pct_debt_below_90, d1_nonaccrual_pct_cost, d4_nonaccrual_pct_cost, d4_pik_share, d4_debt_mark,
    quality_score, quality_trend_1q, quality_trend_4q, generosity, n_shared,
    n_peer_nonaccrual_not_flagged, nav_per_share, nav_period, net_assets, nav_chg_4q, price_date,
    price, div_ttm, ret_3m, ret_6m, ret_12m, p_nav, div_yield, quadrant, short_score, long_score
"""


@app.get("/api/status")
def status():
    files = db.rows("SELECT source_file, loaded_at, n_sub, n_num, n_txt FROM raw.loaded_files ORDER BY source_file")
    counts = db.one(
        """
        SELECT (SELECT count(*) FROM core.holdings) AS holdings,
               (SELECT count(*) FROM core.loans) AS loans,
               (SELECT count(DISTINCT cik) FROM core.holdings) AS bdcs,
               (SELECT count(*) FROM market.screen) AS screened,
               (SELECT max(period_end) FROM core.holdings) AS latest_period,
               (SELECT max(date) FROM market.prices) AS latest_price_date
        """
    )
    recon = db.rows(
        """
        SELECT CASE WHEN override_note IS NOT NULL OR coverage BETWEEN 0.90 AND 1.10 THEN 'reconciles'
                    WHEN coverage IS NULL THEN 'no total'
                    WHEN coverage < 0.90 THEN 'under' ELSE 'over' END AS status,
               count(*) AS n
        FROM core.reconciliation GROUP BY 1 ORDER BY 2 DESC
        """
    )
    excluded = db.rows("SELECT reason, count(*) AS n FROM core.holdings_excluded GROUP BY 1 ORDER BY 2 DESC")
    return {"files": files, "counts": counts, "reconciliation": recon, "excluded": excluded}


FORCED_COLS = """
    fs.coverage AS asset_coverage, fs.coverage_source, fs.distance_pts AS coverage_distance_pts,
    fs.near_limit, fs.b90_up_2q, fs.flagged AS forced_seller_flag
"""
FORCED_JOIN = """
    LEFT JOIN (
        SELECT * FROM signals.forced_seller
        QUALIFY row_number() OVER (PARTITION BY cik ORDER BY period_end DESC) = 1
    ) fs ON fs.cik = {alias}.cik
"""


@app.get("/api/screen")
def screen():
    return db.rows(
        f"SELECT sc.*, {FORCED_COLS} FROM (SELECT {SCREEN_COLS} FROM market.screen) sc "
        f"{FORCED_JOIN.format(alias='sc')} ORDER BY sc.short_score DESC"
    )


@app.get("/api/bdcs")
def bdcs(public_only: bool = False):
    where = "WHERE m.has_xbrl" + (" AND m.is_public" if public_only else "")
    return db.rows(
        f"""
        SELECT m.cik, m.name, m.ticker, m.is_public, l.period_end AS latest_period, l.data_ok,
               l.coverage, l.n_holdings, l.n_debt, l.debt_cost, l.debt_mark, l.pct_debt_below_90,
               l.nonaccrual_pct_cost, l.pik_share, l.new_deterioration_rate, l.quality_score,
               l.quality_trend_4q, s.p_nav, s.quadrant
        FROM ref.bdc_master m
        LEFT JOIN signals.bdc_latest l USING (cik)
        LEFT JOIN market.screen s USING (cik)
        {where}
        ORDER BY l.debt_cost DESC NULLS LAST
        """
    )


@app.get("/api/bdcs/{cik}")
def bdc_detail(cik: int):
    bdc = db.one("SELECT cik, name, ticker, is_public, file_no FROM ref.bdc_master WHERE cik = ?", [cik])
    if not bdc:
        raise HTTPException(404, "unknown BDC")
    quarters = db.rows(
        """
        SELECT q.*, r.coverage AS recon_coverage, r.total_fv, r.detail_fv,
               n.nav_per_share, n.net_assets, p.price_at_period_end,
               p.price_at_period_end / nullif(n.nav_per_share, 0) AS p_nav
        FROM signals.bdc_quarter q
        LEFT JOIN core.reconciliation r ON r.cik = q.cik AND r.period_end = q.period_end
        LEFT JOIN market.nav n ON n.cik = q.cik AND n.period_end = q.period_end
        LEFT JOIN market.pnav_history p ON p.cik = q.cik AND p.period_end = q.period_end
        WHERE q.cik = ? ORDER BY q.period_end
        """,
        [cik],
    )
    migration = db.rows(
        "SELECT period_end, from_bucket, to_bucket, n, cost FROM signals.migration WHERE cik = ? ORDER BY period_end",
        [cik],
    )
    generosity = db.rows(
        "SELECT * FROM signals.bdc_generosity WHERE cik = ? ORDER BY period_end", [cik]
    )
    screen_row = db.one(f"SELECT {SCREEN_COLS} FROM market.screen WHERE cik = ?", [cik])
    scorecard = db.one("SELECT * FROM signals.lender_scorecard WHERE cik = ?", [cik])
    forced = db.rows(
        """
        SELECT period_end, coverage, coverage_source, distance_pts, near_limit, b90_up_2q, flagged,
               pct_debt_below_90, exit_loss_fwd2, fwd2_observed
        FROM signals.forced_seller WHERE cik = ? ORDER BY period_end
        """,
        [cik],
    )
    forced_test = db.rows("SELECT * FROM signals.forced_seller_test")
    return {"bdc": bdc, "quarters": quarters, "migration": migration, "generosity": generosity,
            "screen": screen_row, "scorecard": scorecard, "forced_seller": forced, "forced_seller_test": forced_test}


@app.get("/api/bdcs/{cik}/loans")
def bdc_loans(
    cik: int,
    period: str | None = None,
    flag: str | None = Query(None, description="stressed|nonaccrual|new_nonaccrual|markdown|new|pik|all"),
    limit: int = 5000,
):
    if period is None:
        p = db.one("SELECT max(period_end) AS p FROM core.holdings WHERE cik = ?", [cik])
        if not p or not p["p"]:
            raise HTTPException(404, "no holdings")
        period = str(p["p"])
    cond = {
        None: "TRUE",
        "all": "TRUE",
        "stressed": "is_stressed",
        "nonaccrual": "nonaccrual_flag",
        "new_nonaccrual": "new_nonaccrual",
        "markdown": "mark_chg < -0.02",
        "new": "is_new",
        "pik": "pik_flag",
        "debt": "is_debt",
    }.get(flag, "TRUE")
    return db.rows(
        f"""
        SELECT loan_id, match_method, identifier, issuer_name, issuer_norm, instrument_type,
               instrument_subtype, is_debt, industry, fair_value, cost, principal, mark, prev_mark,
               mark_chg, mark_bucket, prev_bucket, rate, spread, floor_rate, pik_rate, maturity,
               nonaccrual_flag, new_nonaccrual, pik_flag, new_pik, spread_up, maturity_extended,
               converted_to_equity, is_stressed, is_new, obs_n, footnote_text
        FROM signals.loan_quarter
        WHERE cik = ? AND period_end = ? AND {cond}
        ORDER BY is_debt DESC, mark ASC NULLS LAST, cost DESC NULLS LAST
        LIMIT ?
        """,
        [cik, period, limit],
    )


@app.get("/api/loans/{loan_id}")
def loan_detail(loan_id: str):
    loan = db.one(
        """
        SELECT l.*, m.name AS bdc_name, m.ticker FROM core.loans l JOIN ref.bdc_master m USING (cik)
        WHERE loan_id = ?
        """,
        [loan_id],
    )
    if not loan:
        raise HTTPException(404, "unknown loan")
    history = db.rows(
        """
        SELECT period_end, adsh, identifier, instrument_type, fair_value, cost, principal, mark,
               mark_chg, mark_bucket, rate, spread, floor_rate, pik_rate, cash_rate, maturity,
               nonaccrual_flag, pik_flag, spread_up, maturity_extended, match_method, footnote_text,
               pct_net_assets
        FROM signals.loan_quarter WHERE loan_id = ? ORDER BY period_end
        """,
        [loan_id],
    )
    peers = db.rows(
        """
        SELECT b.cik, m.ticker, m.name, b.period_end, b.instrument_type, b.fv, b.cost, b.mark,
               b.nonaccrual, b.mark_vs_peers
        FROM signals.borrower_marks b JOIN ref.bdc_master m USING (cik)
        WHERE b.borrower_key = ? ORDER BY b.period_end DESC, b.mark
        """,
        [loan["borrower_key"]],
    )
    risk = db.rows(
        "SELECT period_end, risk_score, reasons FROM signals.loan_risk WHERE loan_id = ? ORDER BY period_end",
        [loan_id],
    )
    return {"loan": loan, "history": history, "peers": peers, "risk": risk}


@app.get("/api/borrowers")
def borrowers(q: str = Query("", max_length=80), limit: int = 50):
    """Borrower search; with an empty query, the borrowers held by the most BDCs."""
    return db.rows(
        """
        SELECT issuer_norm AS borrower_key, mode(issuer_name) AS issuer_name,
               count(DISTINCT cik) AS n_bdcs, count(*) AS n_loans,
               max(last_period) AS last_period
        FROM core.loans
        WHERE issuer_norm <> ''
        GROUP BY 1
        HAVING ? = '' OR borrower_key ILIKE '%' || ? || '%' OR issuer_name ILIKE '%' || ? || '%'
        ORDER BY n_bdcs DESC, n_loans DESC, borrower_key LIMIT ?
        """,
        [q, q, q, limit],
    )


@app.get("/api/borrowers/{key}")
def borrower_detail(key: str):
    loans = db.rows(
        """
        SELECT l.loan_id, l.cik, m.ticker, m.name AS bdc_name, l.issuer_name, l.instrument_type,
               l.is_debt, l.first_period, l.last_period, l.n_periods, l.last_fair_value, l.last_cost,
               l.last_mark, l.min_mark, l.ever_nonaccrual, l.ever_pik, l.exited, l.exit_type
        FROM core.loans l JOIN ref.bdc_master m USING (cik)
        WHERE l.borrower_key = ? ORDER BY l.last_period DESC, l.last_cost DESC
        """,
        [key],
    )
    if not loans:
        raise HTTPException(404, "unknown borrower")
    marks = db.rows(
        """
        SELECT b.period_end, b.cik, m.ticker, m.name AS bdc_name, b.instrument_type, b.fv, b.cost,
               b.mark, b.nonaccrual, b.n_bdcs, b.avg_mark, b.mark_vs_peers
        FROM signals.borrower_marks b JOIN ref.bdc_master m USING (cik)
        WHERE b.borrower_key = ? ORDER BY b.period_end, b.mark
        """,
        [key],
    )
    return {"borrower_key": key, "loans": loans, "marks": marks}


@app.get("/api/insights/watchlists")
def watchlists():
    stocks = db.rows(
        """
        SELECT cik, ticker, name, side, quadrant, quality_score, quality_trend_4q, validated_score, wavg_risk,
               pct_debt_below_90, nonaccrual_pct_cost, pik_share, new_deterioration_rate, generosity,
               late_mark_rate, early_warning_rate, loss_exit_rate, p_nav, ret_6m, ret_12m, div_yield,
               nav_chg_4q, signal_period, reasons
        FROM signals.watchlist_stocks ORDER BY side NULLS LAST, validated_score DESC NULLS LAST
        """
    )
    loans = db.rows(
        """
        SELECT list, loan_id, cik, ticker, bdc_name, is_public, issuer_name, issuer_norm AS borrower_key,
               industry, instrument_type, period_end, cost, fair_value, mark, risk_score, reasons,
               n_bdcs, peer_avg_mark, mark_vs_peers
        FROM signals.watchlist_loans
        ORDER BY list, risk_score DESC, cost DESC
        """
    )
    return {"stocks": stocks, "loans": loans}


@app.get("/api/insights/validation")
def validation():
    return {
        "meta": db.one("SELECT base_rate, n_loan_quarters, fitted_on FROM signals.model_meta"),
        "loan_signals": db.rows("SELECT * FROM signals.signal_validation ORDER BY lift DESC"),
        "bdc_backtest": db.rows("SELECT * FROM signals.bdc_backtest ORDER BY weight DESC"),
    }


@app.get("/api/insights/scorecards")
def scorecards():
    return db.rows(
        "SELECT * FROM signals.lender_scorecard WHERE n_evaluated >= 50 ORDER BY is_public DESC, late_mark_rate DESC NULLS LAST"
    )


@app.get("/api/insights/sectors")
def sectors():
    # latest quarter that most BDCs have filed (a new quarter starts with a handful of early filers)
    latest = db.one(
        """
        WITH t AS (SELECT period_end, sum(cost) AS c FROM signals.sector_quarter GROUP BY 1)
        SELECT max(period_end) AS p FROM t WHERE c >= 0.5 * (SELECT max(c) FROM t)
        """
    )["p"]
    return {
        "latest_period": latest,
        "sectors": db.rows(
            """
            SELECT s.*, s.pct_stressed - lag(s.pct_stressed, 4) OVER (PARTITION BY industry ORDER BY period_end) AS d4_pct_stressed
            FROM signals.sector_quarter s
            QUALIFY period_end = ? AND cost > 1e8 ORDER BY wavg_risk DESC NULLS LAST
            """,
            [latest],
        ),
        "sector_history": db.rows(
            "SELECT industry, period_end, cost, mark, pct_stressed, pct_nonaccrual, wavg_risk FROM signals.sector_quarter "
            "WHERE industry IN (SELECT industry FROM signals.sector_quarter WHERE period_end = ? AND cost > 1e9) ORDER BY 1, 2",
            [latest],
        ),
        "vintages": db.rows(
            "SELECT * FROM signals.vintage_quarter WHERE period_end = ? AND vintage >= 2019 ORDER BY vintage", [latest]
        ),
    }


@app.get("/api/strategy")
def strategy():
    """The default strategy: its record by quarter, the live book, and headline statistics."""
    return {
        "summary": db.one("SELECT * FROM signals.strategy_summary"),
        "periods": db.rows("SELECT * FROM signals.strategy_periods ORDER BY period_end"),
        "book": db.rows(
            """
            SELECT cik, ticker, name, side, rank, n, health, period_end, filed, entry_date, close_entry, p_nav,
                   pct_debt_below_90, pct_debt_below_95, debt_mark, nav_chg_4q,
                   pct_debt_below_90_rank, pct_debt_below_95_rank, debt_mark_rank, nav_chg_4q_rank
            FROM signals.strategy_latest ORDER BY rank
            """
        ),
        "signals": db.rows(
            "SELECT signal, n_periods, mean_ic, ic_tstat, ic_hit_rate, mean_spread_dir, ann_spread_net, avg_hold_days "
            "FROM signals.bt_event_summary ORDER BY ic_tstat DESC"
        ),
    }


@app.get("/api/stale-marks")
def stale_marks():
    """Stale-mark detector: lender disagreement on shared borrowers, per-BDC generosity and
    no-second-opinion share, and the forward test of generosity."""
    latest = db.one("SELECT max(period_end) AS p FROM signals.stale_bdc s JOIN ref.bdc_master m USING (cik) WHERE m.is_public")["p"]
    return {
        "latest_period": latest,
        "summary": db.rows("SELECT * FROM signals.stale_test_summary"),
        "periods": db.rows("SELECT * FROM signals.stale_test_periods ORDER BY qtr, outcome"),
        "bdcs": db.rows(
            """
            WITH last AS (
                SELECT s.*, row_number() OVER (PARTITION BY s.cik ORDER BY s.period_end DESC) AS rn
                FROM signals.stale_bdc s
            ),
            g4 AS (
                SELECT cik, avg(generosity) AS generosity_4q
                FROM (SELECT cik, generosity, row_number() OVER (PARTITION BY cik ORDER BY period_end DESC) AS rn
                      FROM signals.stale_bdc WHERE generosity IS NOT NULL)
                WHERE rn <= 4 GROUP BY 1
            )
            SELECT l.cik, m.ticker, m.name, m.is_public, l.period_end, l.debt_cost, l.n_shared, l.shared_cost_share,
                   l.no_second_opinion_share, l.own_mark_shared, l.peer_mark_shared, l.generosity, g4.generosity_4q,
                   l.n_above_5, l.n_below_5
            FROM last l JOIN ref.bdc_master m USING (cik) LEFT JOIN g4 USING (cik)
            WHERE l.rn = 1 AND l.period_end >= ? - INTERVAL 200 DAY
            ORDER BY l.generosity DESC NULLS LAST
            """,
            [latest],
        ),
        "borrowers": db.rows(
            """
            SELECT borrower_key, period_end, instrument_type, issuer_name, industry, n_bdcs, n_public, total_cost,
                   wavg_mark, low_mark, high_mark, gap, low_cik, low_lender, high_cik, high_lender,
                   any_nonaccrual, n_nonaccrual, lenders
            FROM signals.stale_borrowers WHERE period_end = ?
            ORDER BY gap DESC, total_cost DESC LIMIT 600
            """,
            [latest],
        ),
    }


LAB_BOOK_COLS = """
    cik, ticker, name, side, rank, n, health, period_end, filed, entry_date, close_entry, p_nav,
    pct_debt_below_90, pct_debt_below_95, debt_mark, nav_chg_4q,
    pct_debt_below_90_rank, pct_debt_below_95_rank, debt_mark_rank, nav_chg_4q_rank
"""


@app.get("/api/lab")
def lab():
    """Strategy lab: liquidity floors with borrow costs, the price/NAV residual, conviction
    weights, extra inputs, turnover; plus today's large-cap and five-input books."""
    return {
        "variants": db.rows("SELECT * FROM signals.lab_variant_summary ORDER BY rowid"),
        "periods": db.rows("SELECT * FROM signals.lab_variant_periods ORDER BY variant, qtr"),
        "signals": db.rows("SELECT * FROM signals.lab_signal_summary ORDER BY ic_tstat DESC"),
        "book_largecap": db.rows(f"SELECT {LAB_BOOK_COLS}, dollar_vol, rank_lc, n_lc, side_lc FROM signals.lab_book_largecap ORDER BY rank_lc"),
        "book_5": db.rows(f"SELECT {LAB_BOOK_COLS}, generosity, generosity_rank, health5, rank5, n5, side5 FROM signals.lab_book_5 ORDER BY rank5"),
        "risk_fits": db.rows("SELECT * FROM signals.lab_risk_fits WHERE fit_qtr = (SELECT max(fit_qtr) FROM signals.lab_risk_fits) ORDER BY weight DESC"),
    }


@app.get("/api/health")
def health():
    return {"ok": True}
