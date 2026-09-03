"""Price / NAV overlay and the long-short screen."""
from __future__ import annotations

import duckdb

from soi.db import table_exists


def build_screen(con: duckdb.DuckDBPyConnection) -> str:
    if not table_exists(con, "market", "prices"):
        con.execute(
            "CREATE TABLE market.prices (ticker VARCHAR, date DATE, close DOUBLE, adj_close DOUBLE, "
            "volume DOUBLE, dividend DOUBLE)"
        )
    if not table_exists(con, "market", "nav"):
        from soi.ingest.prices import build_nav

        build_nav(con)

    con.execute(
        """
        CREATE OR REPLACE TABLE market.price_points AS
        WITH p AS (
            SELECT ticker, date, close, adj_close,
                   sum(dividend) OVER (PARTITION BY ticker ORDER BY date
                                       RANGE BETWEEN INTERVAL 365 DAY PRECEDING AND CURRENT ROW) AS div_ttm
            FROM market.prices
        ),
        last AS (SELECT ticker, max(date) AS last_date FROM p GROUP BY 1)
        SELECT l.ticker, l.last_date,
               (SELECT close FROM p WHERE p.ticker = l.ticker AND p.date = l.last_date) AS last_close,
               (SELECT div_ttm FROM p WHERE p.ticker = l.ticker AND p.date = l.last_date) AS div_ttm,
               (SELECT adj_close FROM p WHERE p.ticker = l.ticker AND p.date = l.last_date) AS adj_last,
               (SELECT adj_close FROM p WHERE p.ticker = l.ticker AND p.date <= l.last_date - INTERVAL 91 DAY ORDER BY date DESC LIMIT 1) AS adj_3m,
               (SELECT adj_close FROM p WHERE p.ticker = l.ticker AND p.date <= l.last_date - INTERVAL 182 DAY ORDER BY date DESC LIMIT 1) AS adj_6m,
               (SELECT adj_close FROM p WHERE p.ticker = l.ticker AND p.date <= l.last_date - INTERVAL 365 DAY ORDER BY date DESC LIMIT 1) AS adj_12m
        FROM last l
        """
    )
    # price at each period end (last close on/before the period end) for P/NAV history
    con.execute(
        """
        CREATE OR REPLACE TABLE market.pnav_history AS
        SELECT n.cik, m.ticker, n.period_end, n.nav_per_share, n.net_assets,
               (SELECT close FROM market.prices p WHERE p.ticker = m.ticker AND p.date <= n.period_end + INTERVAL 45 DAY
                  AND p.date >= n.period_end - INTERVAL 7 DAY ORDER BY abs(date_diff('day', p.date, n.period_end + INTERVAL 45 DAY)) LIMIT 1) AS price_45d_after,
               (SELECT close FROM market.prices p WHERE p.ticker = m.ticker AND p.date <= n.period_end
                  ORDER BY p.date DESC LIMIT 1) AS price_at_period_end
        FROM market.nav n JOIN ref.bdc_master m USING (cik)
        WHERE m.is_public AND n.nav_per_share IS NOT NULL
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE market.screen AS
        WITH latest_nav AS (
            SELECT cik, arg_max(nav_per_share, period_end) AS nav_per_share,
                   max(period_end) AS nav_period,
                   arg_max(net_assets, period_end) AS net_assets
            FROM market.nav WHERE nav_per_share IS NOT NULL GROUP BY cik
        ),
        nav4 AS (
            SELECT cik, period_end, nav_per_share,
                   nav_per_share / nullif(lag(nav_per_share, 4) OVER (PARTITION BY cik ORDER BY period_end), 0) - 1 AS nav_chg_4q
            FROM market.nav WHERE nav_per_share IS NOT NULL
        ),
        base AS (
            SELECT b.cik, b.ticker, b.name, b.period_end AS signal_period, b.data_ok, b.coverage,
                   b.n_holdings, b.n_debt, b.debt_cost, b.debt_fv, b.debt_mark,
                   b.pct_debt_below_95, b.pct_debt_below_90, b.pct_debt_below_80,
                   b.nonaccrual_pct_cost, b.nonaccrual_pct_fv, b.n_nonaccrual, b.pik_share,
                   b.new_deterioration_rate, b.new_nonaccrual_rate, b.markdown_share, b.markup_share,
                   b.exit_loss_rate, b.wavg_spread, b.wavg_rate,
                   b.d1_pct_debt_below_90, b.d4_pct_debt_below_90,
                   b.d1_nonaccrual_pct_cost, b.d4_nonaccrual_pct_cost, b.d4_pik_share, b.d4_debt_mark,
                   b.quality_score, b.quality_trend_1q, b.quality_trend_4q,
                   g.generosity, g.n_shared, g.n_peer_nonaccrual_not_flagged,
                   ln.nav_per_share, ln.nav_period, ln.net_assets,
                   n4.nav_chg_4q,
                   pp.last_date AS price_date, pp.last_close AS price, pp.div_ttm,
                   pp.adj_last / nullif(pp.adj_3m, 0) - 1 AS ret_3m,
                   pp.adj_last / nullif(pp.adj_6m, 0) - 1 AS ret_6m,
                   pp.adj_last / nullif(pp.adj_12m, 0) - 1 AS ret_12m,
                   pp.last_close / nullif(ln.nav_per_share, 0) AS p_nav,
                   pp.div_ttm / nullif(pp.last_close, 0) AS div_yield
            FROM signals.bdc_latest b
            LEFT JOIN signals.bdc_generosity g ON g.cik = b.cik AND g.period_end = b.period_end
            LEFT JOIN latest_nav ln ON ln.cik = b.cik
            LEFT JOIN nav4 n4 ON n4.cik = b.cik AND n4.period_end = ln.nav_period
            LEFT JOIN market.price_points pp ON pp.ticker = b.ticker
            WHERE b.is_public
        ),
        ranked AS (
            SELECT base.*,
                   percent_rank() OVER (ORDER BY p_nav) AS p_nav_pct,
                   percent_rank() OVER (ORDER BY quality_score) AS quality_pct,
                   percent_rank() OVER (ORDER BY quality_trend_4q) AS trend_pct,
                   percent_rank() OVER (ORDER BY ret_6m) AS ret_6m_pct,
                   median(p_nav) OVER () AS p_nav_median,
                   median(ret_6m) OVER () AS ret_6m_median,
                   median(quality_score) OVER () AS quality_median
            FROM base
            WHERE quality_score IS NOT NULL AND p_nav IS NOT NULL
        )
        SELECT ranked.*,
               CASE
                   WHEN quality_trend_4q > 0.25 AND quality_score > quality_median
                        AND p_nav >= p_nav_median AND ret_6m >= ret_6m_median - 0.05 THEN 'short_candidate'
                   WHEN quality_score < quality_median AND coalesce(quality_trend_4q, 0) <= 0.1
                        AND p_nav_pct <= 0.3 THEN 'long_candidate'
                   WHEN quality_trend_4q > 0.25 THEN 'deteriorating_priced'
                   WHEN p_nav_pct <= 0.3 THEN 'cheap_but_messy'
                   ELSE 'neutral'
               END AS quadrant,
               -- short score: worse & worsening book, richer price, price not yet reacting
               coalesce(quality_pct, 0.5) + coalesce(trend_pct, 0.5) + coalesce(p_nav_pct, 0.5)
                   + coalesce(ret_6m_pct, 0.5) AS short_score,
               (1 - coalesce(quality_pct, 0.5)) + (1 - coalesce(trend_pct, 0.5)) + (1 - coalesce(p_nav_pct, 0.5))
                   AS long_score
        FROM ranked
        ORDER BY short_score DESC
        """
    )
    n = con.execute("SELECT count(*) FROM market.screen").fetchone()[0]
    q = con.execute(
        "SELECT quadrant, count(*) FROM market.screen GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return f"market.screen: {n} public BDCs; quadrants: {q}"
