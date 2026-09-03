"""Link holdings across quarters within a BDC into stable loan ids.

Match order between consecutive observed periods of the same BDC:
  1. exact identifier text (+ legal entity)
  2. ident_key (identifier with percentages / whitespace normalised)
  3. (issuer_norm, instrument_type, instrument_subtype, maturity) unique on both sides
  4. (issuer_norm, instrument_type) unique on both sides
  5. fuzzy issuer_norm (rapidfuzz >= 92) with unique instrument_type on both sides
Unmatched current rows start a new loan; unmatched prior rows are exits.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import duckdb
import polars as pl
from rapidfuzz import fuzz, process

HOLDING_KEY = ["cik", "period_end", "adsh", "identifier", "legal_entity"]


@dataclass
class Row:
    idx: int
    identifier: str
    legal_entity: str
    ident_key: str
    issuer_norm: str
    instrument_type: str
    instrument_subtype: str | None
    maturity: object
    fair_value: float | None
    cost: float | None


def _rows(df: pl.DataFrame) -> list[Row]:
    return [
        Row(
            idx=i,
            identifier=r["identifier"],
            legal_entity=r["legal_entity"] or "",
            ident_key=r["ident_key"] or "",
            issuer_norm=r["issuer_norm"] or "",
            instrument_type=r["instrument_type"] or "unknown",
            instrument_subtype=r["instrument_subtype"],
            maturity=r["maturity"],
            fair_value=r["fair_value"],
            cost=r["cost"],
        )
        for i, r in enumerate(df.iter_rows(named=True))
    ]


def _unique_index(rows: list[Row], keyfn) -> dict:
    buckets: dict = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is not None:
            buckets[k].append(r)
    return {k: v[0] for k, v in buckets.items() if len(v) == 1}


def _match_period(prev: list[Row], cur: list[Row]) -> dict[int, tuple[int, str]]:
    """Return {cur.idx: (prev.idx, method)}."""
    out: dict[int, tuple[int, str]] = {}
    used: set[int] = set()

    def stage(name, keyfn, unique_only: bool):
        nonlocal prev, cur
        remaining_prev = [r for r in prev if r.idx not in used]
        remaining_cur = [r for r in cur if r.idx not in out]
        if unique_only:
            pidx = _unique_index(remaining_prev, keyfn)
            cidx = _unique_index(remaining_cur, keyfn)
            for k, c in cidx.items():
                p = pidx.get(k)
                if p is not None:
                    out[c.idx] = (p.idx, name)
                    used.add(p.idx)
        else:
            buckets: dict = defaultdict(list)
            for r in remaining_prev:
                buckets[keyfn(r)].append(r)
            for c in remaining_cur:
                cands = buckets.get(keyfn(c))
                if not cands:
                    continue
                # several prior rows share the key (e.g. "Loan 1"/"Loan 2" collapsed): pick
                # the one with the closest cost
                best = min(
                    cands,
                    key=lambda p: abs((p.cost or p.fair_value or 0) - (c.cost or c.fair_value or 0)),
                )
                out[c.idx] = (best.idx, name)
                used.add(best.idx)
                cands.remove(best)

    stage("exact", lambda r: (r.identifier, r.legal_entity), unique_only=False)
    stage("ident_key", lambda r: (r.ident_key, r.legal_entity), unique_only=False)
    stage(
        "issuer_type_mat",
        lambda r: (r.issuer_norm, r.instrument_type, r.instrument_subtype, r.maturity)
        if r.issuer_norm else None,
        unique_only=True,
    )
    stage(
        "issuer_type",
        lambda r: (r.issuer_norm, r.instrument_type) if r.issuer_norm else None,
        unique_only=True,
    )

    # fuzzy issuer names for what is left (unique instrument type per issuer on both sides)
    remaining_prev = [r for r in prev if r.idx not in used and r.issuer_norm]
    remaining_cur = [r for r in cur if r.idx not in out and r.issuer_norm]
    if remaining_prev and remaining_cur:
        prev_by_name: dict[str, list[Row]] = defaultdict(list)
        for r in remaining_prev:
            prev_by_name[r.issuer_norm].append(r)
        names = list(prev_by_name)
        for c in remaining_cur:
            hit = process.extractOne(c.issuer_norm, names, scorer=fuzz.token_set_ratio, score_cutoff=92)
            if not hit:
                continue
            cands = [p for p in prev_by_name[hit[0]] if p.idx not in used
                     and p.instrument_type == c.instrument_type]
            if len(cands) == 1:
                out[c.idx] = (cands[0].idx, "fuzzy")
                used.add(cands[0].idx)
    return out


def build_loans(con: duckdb.DuckDBPyConnection) -> str:
    cols = HOLDING_KEY + [
        "ident_key", "issuer_norm", "instrument_type", "instrument_subtype", "maturity",
        "fair_value", "cost",
    ]
    df = con.execute(
        f"SELECT {', '.join(cols)} FROM core.holdings ORDER BY cik, period_end"
    ).pl()

    loan_ids: list[str | None] = [None] * df.height
    methods: list[str | None] = [None] * df.height
    seq_by_cik: dict[int, int] = defaultdict(int)

    # global row offset bookkeeping: work per cik, per period
    offset = 0
    for (cik,), g in df.group_by(["cik"], maintain_order=True):
        periods = g["period_end"].unique(maintain_order=True).to_list()
        prev_rows: list[Row] = []
        prev_loan: dict[int, str] = {}
        prev_offset = 0
        cur_offset = offset
        for p in periods:
            gp = g.filter(pl.col("period_end") == p)
            cur_rows = _rows(gp)
            matches = _match_period(prev_rows, cur_rows) if prev_rows else {}
            cur_loan: dict[int, str] = {}
            for r in cur_rows:
                m = matches.get(r.idx)
                if m is not None:
                    lid = prev_loan[m[0]]
                    methods[cur_offset + r.idx] = m[1]
                else:
                    seq_by_cik[cik] += 1
                    lid = f"{cik}-{seq_by_cik[cik]}"
                    methods[cur_offset + r.idx] = "new"
                cur_loan[r.idx] = lid
                loan_ids[cur_offset + r.idx] = lid
            prev_rows, prev_loan, prev_offset = cur_rows, cur_loan, cur_offset
            cur_offset += gp.height
        offset = cur_offset
    _ = prev_offset

    linked = df.select(HOLDING_KEY).with_columns(
        pl.Series("loan_id", loan_ids), pl.Series("match_method", methods)
    )
    con.register("linked", linked)
    con.execute(
        """
        CREATE OR REPLACE TABLE core.loan_history AS
        SELECT l.loan_id, l.match_method, h.*
        FROM core.holdings h
        JOIN linked l ON l.cik = h.cik AND l.period_end = h.period_end AND l.adsh = h.adsh
             AND l.identifier = h.identifier AND l.legal_entity = h.legal_entity
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE core.loans AS
        WITH latest_period AS (
            SELECT cik, max(period_end) AS bdc_latest FROM core.holdings GROUP BY cik
        ),
        agg AS (
            SELECT loan_id, cik,
                   arg_max(issuer_name, period_end) AS issuer_name,
                   arg_max(issuer_norm, period_end) AS issuer_norm,
                   arg_max(instrument_type, period_end) AS instrument_type,
                   arg_max(instrument_subtype, period_end) AS instrument_subtype,
                   arg_max(is_debt, period_end) AS is_debt,
                   arg_max(identifier, period_end) AS identifier,
                   arg_max(industry, period_end) AS industry,
                   min(period_end) AS first_period, max(period_end) AS last_period,
                   count(*) AS n_periods,
                   arg_max(fair_value, period_end) AS last_fair_value,
                   arg_max(cost, period_end) AS last_cost,
                   arg_max(mark, period_end) AS last_mark,
                   min(mark) AS min_mark,
                   bool_or(nonaccrual_flag) AS ever_nonaccrual,
                   bool_or(pik_flag) AS ever_pik
            FROM core.loan_history GROUP BY loan_id, cik
        )
        SELECT a.*, a.issuer_norm AS borrower_key,
               a.last_period < lp.bdc_latest AS exited,
               CASE WHEN a.last_period < lp.bdc_latest AND a.last_mark < 0.9 THEN 'loss'
                    WHEN a.last_period < lp.bdc_latest THEN 'repaid_or_sold' END AS exit_type
        FROM agg a JOIN latest_period lp USING (cik)
        """
    )
    stats = con.execute(
        """
        SELECT match_method, count(*) FROM core.loan_history GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()
    n_loans = con.execute("SELECT count(*) FROM core.loans").fetchone()[0]
    return f"core.loans: {n_loans:,} loans; match methods: {stats}"
