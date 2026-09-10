"""Export every API response the web UI needs as static JSON.

The output tree mirrors ``api/main.py`` so the React app can run without a server
(see ``web/src/lib/api.ts`` for the client-side routing, decoding and filtering)::

    status.json, screen.json, bdcs.json, manifest.json
    bdcs/{cik}.json                 -> /api/bdcs/{cik}
    bdcs/{cik}/loans/{period}.json  -> /api/bdcs/{cik}/loans?period=..   (flag=all; client filters)
    loans/{shard}.json              -> {loan_id: {loan, history}}        (4096 FNV-1a shards)
    borrowers/index.json            -> search index for /api/borrowers?q=
    borrowers/{shard}.json          -> {borrower_key: {loans, marks}}    (256 FNV-1a shards)

Two encodings keep the tree small enough to host:

* Big tables are columnar: ``{"cols": [...], "rows": [[...], ...]}`` (key names were half
  the bytes).
* ``footnote_text`` is replaced by ``fn``, an index into a per-file ``footnotes`` list.

Loan-detail "peers" come from the borrower shard on the client, so peer rows are not
duplicated per loan. This module only reads the database; it never modifies it.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import shutil
import sys
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

LOAN_SHARDS = 4096
BORROWER_SHARDS = 256

LOAN_LIST_COLS = [
    "loan_id", "match_method", "identifier", "issuer_name", "issuer_norm", "instrument_type",
    "instrument_subtype", "is_debt", "industry", "fair_value", "cost", "principal", "mark",
    "prev_mark", "mark_chg", "mark_bucket", "prev_bucket", "rate", "spread", "floor_rate",
    "pik_rate", "maturity", "nonaccrual_flag", "new_nonaccrual", "pik_flag", "new_pik",
    "spread_up", "maturity_extended", "converted_to_equity", "is_stressed", "is_new", "obs_n",
    "footnote_text",
]
HISTORY_COLS = [
    "period_end", "adsh", "identifier", "instrument_type", "fair_value", "cost", "principal",
    "mark", "mark_chg", "mark_bucket", "rate", "spread", "floor_rate", "pik_rate", "cash_rate",
    "maturity", "nonaccrual_flag", "pik_flag", "spread_up", "maturity_extended", "match_method",
    "footnote_text", "pct_net_assets",
]
BORROWER_LOAN_COLS = [
    "loan_id", "cik", "ticker", "bdc_name", "issuer_name", "instrument_type", "is_debt",
    "first_period", "last_period", "n_periods", "last_fair_value", "last_cost", "last_mark",
    "min_mark", "ever_nonaccrual", "ever_pik", "exited", "exit_type",
]
BORROWER_MARK_COLS = [
    "period_end", "cik", "ticker", "bdc_name", "instrument_type", "fv", "cost", "mark",
    "nonaccrual", "n_bdcs", "avg_mark", "mark_vs_peers",
]


def fnv1a(s: str) -> int:
    """32-bit FNV-1a over UTF-8 bytes; must match ``fnv1a`` in web/src/lib/api.ts."""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def loan_shard(loan_id: str) -> str:
    return format(fnv1a(loan_id) % LOAN_SHARDS, "03x")


def borrower_shard(key: str) -> str:
    return format(fnv1a(key) % BORROWER_SHARDS, "02x")


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 6)
    if isinstance(v, Decimal):
        return _clean(float(v))
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _row(d: dict[str, Any]) -> dict[str, Any]:
    """Object row: drop nulls (the UI treats missing and null alike), normalise scalars."""
    out = {}
    for k, v in d.items():
        v = _clean(v)
        if v is not None:
            out[k] = v
    return out


def _rows(rs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_row(r) for r in rs]


class FootnoteDict:
    """Per-file dictionary of footnote strings."""

    def __init__(self) -> None:
        self.notes: list[str] = []
        self._idx: dict[str, int] = {}

    def index(self, text: Any) -> int | None:
        if text is None:
            return None
        if text not in self._idx:
            self._idx[text] = len(self.notes)
            self.notes.append(text)
        return self._idx[text]


def _table(rows: list[dict[str, Any]], cols: list[str], fnd: FootnoteDict | None = None) -> dict:
    """Columnar encoding; ``footnote_text`` becomes an ``fn`` index when ``fnd`` is given."""
    out_cols = [("fn" if c == "footnote_text" and fnd else c) for c in cols]
    data = []
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c)
            vals.append(fnd.index(v) if (c == "footnote_text" and fnd) else _clean(v))
        data.append(vals)
    return {"cols": out_cols, "rows": data}


def _dump(path: Path, obj: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    path.write_text(s, encoding="utf-8")
    return len(s.encode("utf-8"))


def _with_master(cols: list[str], alias: str) -> str:
    """Column list where ticker/bdc_name come from the joined master table."""
    return ", ".join(c if c in ("ticker", "bdc_name") else f"{alias}.{c}" for c in cols)


MASTER = "(SELECT cik, ticker, name AS bdc_name FROM ref.bdc_master)"


def export_static(out: Path, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Write the static JSON tree under ``out`` (wiped first). Returns the manifest."""
    from soi.config import PROJECT_ROOT

    if str(PROJECT_ROOT) not in sys.path:  # `api/` lives next to src/, not inside the package
        sys.path.insert(0, str(PROJECT_ROOT))
    from api import db
    from api import main as api

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    n_files = 0
    n_bytes = 0

    def put(rel: str, obj: Any) -> None:
        nonlocal n_files, n_bytes
        n_bytes += _dump(out / rel, obj)
        n_files += 1

    # --- top-level -------------------------------------------------------------------------
    status = api.status()
    put("status.json", {
        "files": _rows(status["files"]),
        "counts": _row(status["counts"]),
        "reconciliation": _rows(status["reconciliation"]),
        "excluded": _rows(status["excluded"]),
    })
    put("screen.json", _rows(api.screen()))
    wl = api.watchlists()
    put("insights/watchlists.json", {"stocks": _rows(wl["stocks"]), "loans": _rows(wl["loans"])})
    v = api.validation()
    put("insights/validation.json", {"meta": _row(v["meta"]), "loan_signals": _rows(v["loan_signals"]),
                                     "bdc_backtest": _rows(v["bdc_backtest"])})
    put("insights/scorecards.json", _rows(api.scorecards()))
    st = api.strategy()
    put("strategy.json", {"summary": _row(st["summary"]), "periods": _rows(st["periods"]),
                          "book": _rows(st["book"]), "signals": _rows(st["signals"])})
    sec = api.sectors()
    put("insights/sectors.json", {"latest_period": _clean(sec["latest_period"]), "sectors": _rows(sec["sectors"]),
                                  "sector_history": _rows(sec["sector_history"]), "vintages": _rows(sec["vintages"])})
    sm = api.stale_marks()
    put("stale-marks.json", {"latest_period": _clean(sm["latest_period"]), "summary": _rows(sm["summary"]),
                             "periods": _rows(sm["periods"]), "bdcs": _rows(sm["bdcs"]),
                             "borrowers": _rows(sm["borrowers"])})
    lb = api.lab()
    put("lab.json", {k: _rows(v) for k, v in lb.items()})
    bdcs = api.bdcs(public_only=False)
    put("bdcs.json", _rows(bdcs))
    log(f"top-level: {len(bdcs)} BDCs, {len(status['files'])} source files")

    # --- per-BDC detail ---------------------------------------------------------------------
    ciks = [b["cik"] for b in bdcs]
    for cik in ciks:
        d = api.bdc_detail(cik)
        put(f"bdcs/{cik}.json", {
            "bdc": _row(d["bdc"]),
            "quarters": _rows(d["quarters"]),
            "migration": _rows(d["migration"]),
            "generosity": _rows(d["generosity"]),
            "screen": _row(d["screen"]) if d["screen"] else None,
            "scorecard": _row(d["scorecard"]) if d.get("scorecard") else None,
            "forced_seller": _rows(d.get("forced_seller", [])),
            "forced_seller_test": _rows(d.get("forced_seller_test", [])),
        })
    log(f"bdc detail: {len(ciks)} files")

    # --- per-BDC-period loan lists (same columns/order as /api/bdcs/{cik}/loans) ------------
    lq = db.rows(
        f"""
        SELECT cik, period_end, {", ".join(LOAN_LIST_COLS)}
        FROM signals.loan_quarter
        ORDER BY cik, period_end, is_debt DESC, mark ASC NULLS LAST, cost DESC NULLS LAST
        """
    )
    by_period: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for r in lq:
        by_period[(r["cik"], str(r["period_end"]))].append(r)
    for (cik, period), rows in by_period.items():
        fnd = FootnoteDict()
        t = _table(rows, LOAN_LIST_COLS, fnd)
        put(f"bdcs/{cik}/loans/{period}.json", {"footnotes": fnd.notes, **t})
    log(f"bdc loan lists: {len(by_period)} files, {len(lq):,} rows")
    del lq, by_period

    # --- loan detail shards ---------------------------------------------------------------
    loans = db.rows(
        "SELECT l.*, m.name AS bdc_name, m.ticker FROM core.loans l JOIN ref.bdc_master m USING (cik)"
    )
    history = db.rows(
        f"SELECT loan_id, {', '.join(HISTORY_COLS)} FROM signals.loan_quarter "
        "ORDER BY loan_id, period_end"
    )
    hist_by_loan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in history:
        hist_by_loan[h["loan_id"]].append(h)
    del history
    risk_by_loan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in db.rows("SELECT loan_id, period_end, risk_score, reasons FROM signals.loan_risk ORDER BY loan_id, period_end"):
        risk_by_loan[r["loan_id"]].append({"period_end": _clean(r["period_end"]), "risk_score": r["risk_score"],
                                           "reasons": list(r["reasons"] or [])})
    shards: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in loans:
        shards[loan_shard(ln["loan_id"])].append(ln)
    hist_cols = [("fn" if c == "footnote_text" else c) for c in HISTORY_COLS]
    for shard, lns in shards.items():
        fnd = FootnoteDict()
        entries = {}
        for ln in lns:
            t = _table(hist_by_loan.get(ln["loan_id"], []), HISTORY_COLS, fnd)
            entries[ln["loan_id"]] = {"loan": _row(ln), "history": t["rows"],
                                      "risk": risk_by_loan.get(ln["loan_id"], [])}
        put(f"loans/{shard}.json",
            {"footnotes": fnd.notes, "history_cols": hist_cols, "loans": entries})
    log(f"loan shards: {len(shards)} files, {len(loans):,} loans")
    del shards, hist_by_loan

    # --- borrowers --------------------------------------------------------------------------
    index = db.rows(
        """
        SELECT issuer_norm AS borrower_key, mode(issuer_name) AS issuer_name,
               count(DISTINCT cik) AS n_bdcs, count(*) AS n_loans, max(last_period) AS last_period
        FROM core.loans WHERE issuer_norm <> '' GROUP BY 1 ORDER BY n_bdcs DESC, n_loans DESC, borrower_key
        """
    )
    put("borrowers/index.json",
        _table(index, ["borrower_key", "issuer_name", "n_bdcs", "n_loans", "last_period"]))
    bloans = db.rows(
        f"""
        SELECT l.borrower_key, {_with_master(BORROWER_LOAN_COLS, "l")}
        FROM core.loans l JOIN {MASTER} m USING (cik)
        ORDER BY l.borrower_key, l.last_period DESC, l.last_cost DESC
        """
    )
    marks = db.rows(
        f"""
        SELECT b.borrower_key, {_with_master(BORROWER_MARK_COLS, "b")}
        FROM signals.borrower_marks b JOIN {MASTER} m USING (cik)
        ORDER BY b.borrower_key, b.period_end, b.mark
        """
    )
    bl_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in bloans:
        bl_by_key[r["borrower_key"]].append(r)
    mk_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in marks:
        mk_by_key[r["borrower_key"]].append(r)
    bshards: dict[str, dict[str, Any]] = defaultdict(dict)
    for key in set(bl_by_key) | set(mk_by_key):
        bshards[borrower_shard(key)][key] = {
            "loans": _table(bl_by_key.get(key, []), BORROWER_LOAN_COLS)["rows"],
            "marks": _table(mk_by_key.get(key, []), BORROWER_MARK_COLS)["rows"],
        }
    for shard, obj in bshards.items():
        put(f"borrowers/{shard}.json", {"loan_cols": BORROWER_LOAN_COLS,
                                        "mark_cols": BORROWER_MARK_COLS, "borrowers": obj})
    log(f"borrowers: index of {len(index):,}, {len(bshards)} shards")

    manifest = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "loan_shards": LOAN_SHARDS,
        "borrower_shards": BORROWER_SHARDS,
        "n_bdcs": len(ciks),
        "n_loans": len(loans),
        "n_borrowers": len(index),
        "n_files": n_files + 1,
        "n_bytes": n_bytes,
        "latest_period": _clean(status["counts"]["latest_period"]),
    }
    put("manifest.json", manifest)
    log(f"wrote {manifest['n_files']:,} files, {n_bytes / 1e6:,.1f} MB to {out}")
    return manifest
