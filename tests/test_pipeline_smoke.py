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
        WHERE m.ticker IN ('ARCC','BXSL','GBDC','MAIN','OBDC','FSK')
          AND r.period_end = (SELECT max(period_end) FROM core.reconciliation r2 WHERE r2.cik = r.cik)
        """
    ).fetchall()
    assert rows, "expected the big public BDCs to be present"
    for ticker, cov in rows:
        assert cov is not None and 0.9 <= cov <= 1.1, (ticker, cov)


def test_loan_history_matches_holdings(con):
    a = con.execute("SELECT count(*) FROM core.holdings").fetchone()[0]
    b = con.execute("SELECT count(*) FROM core.loan_history").fetchone()[0]
    assert a == b


def test_loan_ids_stable_within_bdc(con):
    bad = con.execute(
        "SELECT count(*) FROM (SELECT loan_id, count(DISTINCT cik) c FROM core.loan_history GROUP BY 1 HAVING c > 1)"
    ).fetchone()[0]
    assert bad == 0
