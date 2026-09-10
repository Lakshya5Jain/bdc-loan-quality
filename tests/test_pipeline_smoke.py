"""Smoke checks against the built DuckDB file (skipped when it does not exist)."""
import pytest

from soi.config import settings
from soi.db import connect, table_exists

pytestmark = pytest.mark.skipif(not settings.db_path.exists(), reason="data/soi.duckdb not built")


@pytest.fixture(scope="module")
def con():
    c = connect(read_only=True)
    yield c
    c.close()


def test_core_tables_exist(con):
    for schema, table in [("core", "holdings"), ("core", "loans"), ("core", "loan_history"),
                          ("signals", "bdc_quarter"), ("core", "reconciliation")]:
        assert table_exists(con, schema, table), f"{schema}.{table} missing"


def test_holdings_unique_per_period(con):
    dup = con.execute(
        "SELECT count(*) FROM (SELECT cik, period_end, identifier, legal_entity, count(*) c "
        "FROM core.holdings GROUP BY 1,2,3,4 HAVING c > 1)"
    ).fetchone()[0]
    assert dup == 0


def test_one_filing_per_bdc_period(con):
    dup = con.execute(
        "SELECT count(*) FROM (SELECT cik, period_end, count(DISTINCT adsh) c FROM core.holdings "
        "GROUP BY 1,2 HAVING c > 1)"
    ).fetchone()[0]
    assert dup == 0


def test_large_bdcs_reconcile(con):
    rows = con.execute(
        """
        SELECT m.ticker, r.coverage FROM core.reconciliation r JOIN ref.bdc_master m USING (cik)
        WHERE m.ticker IN ('ARCC','BXSL','GBDC','MAIN','OBDC')
          AND r.period_end = (SELECT max(period_end) FROM core.reconciliation r2 WHERE r2.cik = r.cik)
        """
    ).fetchall()
    assert rows, "expected the big public BDCs to be present"
    for ticker, cov in rows:
        assert cov is not None and 0.95 <= cov <= 1.05, (ticker, cov)


def test_loan_history_matches_holdings(con):
    a = con.execute("SELECT count(*) FROM core.holdings").fetchone()[0]
    b = con.execute("SELECT count(*) FROM core.loan_history").fetchone()[0]
    assert a == b


def test_loan_ids_stable_within_bdc(con):
    bad = con.execute(
        "SELECT count(*) FROM (SELECT loan_id, count(DISTINCT cik) c FROM core.loan_history GROUP BY 1 HAVING c > 1)"
    ).fetchone()[0]
    assert bad == 0


def test_heading_rows_not_in_holdings(con):
    # Space-separated hierarchies ("Debt Investments Healthcare ...") leaked sector headings
    # into holdings; a heading is a word-boundary prefix of >= 2 other rows in the same period.
    n = con.execute(
        """
        SELECT count(*) FROM (
            SELECT p.cik, p.period_end, p.identifier, count(*) AS n_child
            FROM core.holdings p JOIN core.holdings c
              ON c.cik = p.cik AND c.period_end = p.period_end AND c.identifier <> p.identifier
             AND starts_with(c.identifier, p.identifier || ' ')
            WHERE p.cik IN (1786108, 1508655)  -- Trinity, Sixth Street
            GROUP BY 1, 2, 3 HAVING count(*) >= 2
        )
        """
    ).fetchone()[0]
    assert n == 0


def test_no_holding_exceeds_reported_total(con):
    bad = con.execute(
        """
        SELECT count(*) FROM core.holdings h JOIN core.reconciliation r USING (cik, period_end)
        WHERE r.total_fv > 0 AND h.fair_value > 1.05 * r.total_fv
        """
    ).fetchone()[0]
    assert bad == 0


def test_reconciliation_rate(con):
    ok, n = con.execute(
        "SELECT count(*) FILTER (WHERE coverage BETWEEN 0.9 AND 1.1 OR override_note IS NOT NULL), "
        "count(*) FROM core.reconciliation"
    ).fetchone()
    assert ok / n >= 0.8, (ok, n)


def test_public_bdc_reconciliation_rate(con):
    ok, n = con.execute(
        """
        SELECT count(*) FILTER (WHERE coverage BETWEEN 0.9 AND 1.1 OR override_note IS NOT NULL), count(*)
        FROM core.reconciliation r JOIN ref.bdc_master m USING (cik) WHERE m.is_public
        """
    ).fetchone()
    assert ok / n >= 0.95, (ok, n)


def test_member_axis_filers_have_history(con):
    # These public BDCs tag holdings as axis-member combinations rather than the typed
    # identifier axis; before member-axis extraction they had 5-8 periods each.
    rows = con.execute(
        """
        SELECT m.ticker, count(DISTINCT h.period_end) FROM ref.bdc_master m
        JOIN core.holdings h USING (cik)
        WHERE m.ticker IN ('HRZN', 'KBDC', 'OXSQ', 'PFX', 'PSBD', 'SAR', 'TPVG', 'NSLR')
        GROUP BY 1
        """
    ).fetchall()
    assert len(rows) == 8, rows
    assert all(n >= 12 for _, n in rows), rows


def test_no_phantom_periods(con):
    # A period sourced from another filing's prior-period comparatives with a handful of rows
    # (affiliate roll-forward tables) must not survive as a BDC-period.
    bad = con.execute(
        """
        WITH mx AS (SELECT cik, max(n_holdings) AS mx FROM core.reconciliation GROUP BY 1)
        SELECT count(*) FROM core.reconciliation r JOIN mx USING (cik)
        JOIN core.period_source ps USING (cik, period_end)
        WHERE ps.period <> r.period_end AND r.n_holdings < 0.1 * mx.mx AND mx.mx >= 50
        """
    ).fetchone()[0]
    assert bad == 0


def test_foreign_currency_facts_not_double_counted(con):
    # TSLX tags the local-currency par of European loans as a second fair value fact.
    cov = con.execute(
        """
        SELECT r.coverage FROM core.reconciliation r JOIN ref.bdc_master m USING (cik)
        WHERE m.ticker = 'TSLX' ORDER BY r.period_end DESC LIMIT 1
        """
    ).fetchone()[0]
    assert 0.98 <= cov <= 1.02, cov


def test_gsbd_recent_periods_reconcile(con):
    # The bulk data misses 8-12% of GSBD's rows since 2025; `soi ixfacts` fills them from the filing.
    rows = con.execute(
        """
        SELECT period_end, coverage FROM core.reconciliation r JOIN ref.bdc_master m USING (cik)
        WHERE m.ticker = 'GSBD' AND period_end >= DATE '2025-03-31'
        """
    ).fetchall()
    assert rows and all(c >= 0.95 for _, c in rows), rows


def test_loan_links_survive_format_changes(con):
    # share of holdings in a trusted period that continue a loan seen in the previous trusted period
    share, n = con.execute(
        """
        WITH ok AS (SELECT cik, period_end FROM core.reconciliation
                    WHERE coverage BETWEEN 0.9 AND 1.1 OR override_note IS NOT NULL),
        per AS (SELECT cik, period_end, lag(period_end) OVER (PARTITION BY cik ORDER BY period_end) AS prev FROM ok)
        SELECT avg((l.first_period < p.period_end)::INT), count(*)
        FROM per p JOIN core.loan_history h USING (cik, period_end) JOIN core.loans l USING (loan_id)
        JOIN ref.bdc_master m USING (cik) WHERE m.is_public AND p.prev IS NOT NULL
        """
    ).fetchone()
    assert share >= 0.75, (share, n)


def test_quality_score_for_every_sizeable_public_bdc(con):
    missing = con.execute(
        """
        SELECT ticker FROM signals.bdc_latest
        WHERE is_public AND data_ok AND n_debt >= 30 AND quality_score IS NULL
          AND period_end >= (SELECT max(period_end) FROM core.holdings) - INTERVAL 200 DAY
        """
    ).fetchall()
    assert missing == [], missing


def test_debt_rates_are_rates(con):
    bad = con.execute(
        """
        SELECT avg((rate NOT BETWEEN 0.01 AND 0.30)::INT) FROM core.holdings h
        JOIN signals.bdc_latest l USING (cik, period_end)
        WHERE l.is_public AND h.is_debt AND h.rate IS NOT NULL
        """
    ).fetchone()[0]
    assert bad < 0.02, bad


def test_fundamentals_cover_public_bdcs(con):
    n, nii, lev = con.execute(
        """
        SELECT count(*), count(f.nii), count(f.leverage)
        FROM signals.bdc_fundamentals f JOIN signals.bdc_latest l USING (cik, period_end) WHERE l.is_public
        """
    ).fetchone()
    assert nii >= 0.9 * n and lev >= 0.8 * n, (n, nii, lev)


def test_backtest_tables(con):
    n_dates, n_signals = con.execute(
        "SELECT count(DISTINCT rebal_date), count(DISTINCT signal) FROM signals.bt_periods"
    ).fetchone()
    assert n_dates >= 12 and n_signals >= 20, (n_dates, n_signals)
    # no forward return may be measured before the filing was public
    leak = con.execute("SELECT count(*) FROM signals.bt_universe WHERE filed > rebal_date").fetchone()[0]
    assert leak == 0
    leak = con.execute("SELECT count(*) FROM signals.bt_event_universe WHERE filed >= entry_date").fetchone()[0]
    assert leak == 0


def test_event_backtest_buckets_and_benchmark(con):
    # every filing sits in a calendar-quarter bucket (Saratoga's May quarter joins June)
    bad = con.execute(
        "SELECT count(*) FROM signals.bt_event_universe WHERE extract(month FROM qtr) NOT IN (3, 6, 9, 12)"
    ).fetchone()[0]
    assert bad == 0
    assert con.execute("SELECT count(*) FROM signals.bt_event_universe WHERE ticker = 'SAR'").fetchone()[0] >= 8
    # the benchmark is the tradable universe, never a handful of names
    n_min = con.execute("SELECT min(n_bench) FROM signals.bt_event_universe").fetchone()[0]
    assert n_min >= 12, n_min


def test_live_book_skips_missing_inputs(con):
    # a missing input is NULL, not ranked as the best; health averages what exists
    rows = con.execute(
        """
        SELECT ticker, nav_chg_4q, nav_chg_4q_rank, n_inputs, health,
               pct_debt_below_90_rank, pct_debt_below_95_rank, debt_mark_rank
        FROM signals.strategy_latest
        """
    ).fetchall()
    assert rows
    for t, nav, nav_r, n_in, health, r90, r95, rmk in rows:
        if nav is None:
            assert nav_r is None, t
        assert n_in >= 2, t
        parts = [x for x in ((1 - r90) if r90 is not None else None, (1 - r95) if r95 is not None else None, rmk, nav_r)
                 if x is not None]
        assert abs(health - sum(parts) / len(parts)) < 1e-9, t
    ranks = con.execute(
        "SELECT min(pct_debt_below_90_rank), max(pct_debt_below_90_rank) FROM signals.strategy_latest"
    ).fetchone()
    assert ranks == (0.0, 1.0), ranks


def test_nav_change_is_one_year_apart(con):
    # nav_chg_4q compares with the quarter about a year earlier by date, not four rows back
    bad = con.execute(
        """
        WITH f AS (
            SELECT f.cik, f.period_end, f.nav_chg_4q, f.nav_per_share,
                   (SELECT x.nav_per_share FROM signals.bdc_fundamentals x WHERE x.cik = f.cik
                      AND x.period_end BETWEEN f.period_end - INTERVAL 385 DAY AND f.period_end - INTERVAL 345 DAY
                    ORDER BY x.period_end DESC LIMIT 1) AS nav_1y
            FROM signals.bdc_fundamentals f
        )
        SELECT count(*) FROM f
        WHERE nav_chg_4q IS NOT NULL AND (nav_1y IS NULL OR abs(nav_per_share / nav_1y - 1 - nav_chg_4q) > 1e-9)
        """
    ).fetchone()[0]
    assert bad == 0


def test_stale_marks_tables(con):
    # every shared unit has two or more lenders and a non-negative gap between them
    bad = con.execute(
        "SELECT count(*) FROM signals.stale_borrowers WHERE n_bdcs < 2 OR gap < 0 OR gap <> high_mark - low_mark"
    ).fetchone()[0]
    assert bad == 0
    # a BDC's shared cost never exceeds its debt cost; the two shares add to one
    bad = con.execute(
        """
        SELECT count(*) FROM signals.stale_bdc
        WHERE shared_cost > debt_cost * 1.000001
           OR abs(shared_cost_share + no_second_opinion_share - 1) > 1e-9
           OR (n_shared > 0 AND generosity IS NULL)
        """
    ).fetchone()[0]
    assert bad == 0
    # generosity is own mark minus the peers' mark on the same loans, so it is bounded
    lo, hi = con.execute("SELECT min(generosity), max(generosity) FROM signals.stale_bdc").fetchone()
    assert -1 <= lo and hi <= 1, (lo, hi)
    # the test has both outcomes at both horizons, and the periods behind each summary row
    outcomes = {r[0] for r in con.execute("SELECT outcome FROM signals.stale_test_summary").fetchall()}
    assert outcomes == {"nav_chg_fwd1", "nav_chg_fwd2", "b90_chg_fwd1", "b90_chg_fwd2"}
    n = con.execute(
        "SELECT count(*) FROM signals.stale_test_summary s WHERE n_quarters <> "
        "(SELECT count(*) FROM signals.stale_test_periods p WHERE p.outcome = s.outcome)"
    ).fetchone()[0]
    assert n == 0
    # the existing cross-lender tables are untouched by the new step
    assert con.execute("SELECT count(*) FROM signals.bdc_generosity").fetchone()[0] > 0
