"""Decode the exported static tree the way web/src/lib/api.ts does and compare it with the
live API functions. Skipped unless both data/soi.duckdb and web/public/data exist."""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from soi.config import PROJECT_ROOT, settings
from soi.export_static import borrower_shard, loan_shard

DATA = PROJECT_ROOT / "web" / "public" / "data"

pytestmark = pytest.mark.skipif(
    not settings.db_path.exists() or not (DATA / "manifest.json").exists(),
    reason="needs data/soi.duckdb and a fresh `uv run soi export-static`",
)


@pytest.fixture(scope="module")
def api():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from api import main

    return main


def _load(rel: str) -> Any:
    return json.loads((DATA / rel).read_text(encoding="utf-8"))


def _expand(cols: list[str], rows: list[list[Any]], notes: list[str] | None = None) -> list[dict]:
    out = []
    for r in rows:
        o = {}
        for c, v in zip(cols, r, strict=True):
            if c == "fn":
                o["footnote_text"] = None if v is None else notes[v]  # type: ignore[index]
            else:
                o[c] = v
        out.append(o)
    return out


def _norm(v: Any) -> Any:
    """API value -> what the export writes (nulls dropped by callers where relevant)."""
    if isinstance(v, float):
        return None if math.isnan(v) else round(v, 6)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _key(r: dict) -> tuple:
    """Stable identity for a row; SQL ORDER BY ties come back in arbitrary order."""
    return tuple(str(_norm(r.get(k))) for k in ("loan_id", "period_end", "cik", "instrument_type", "cost"))


def _same_rows(api_rows: list[dict], static_rows: list[dict], nulls_dropped: bool) -> None:
    assert len(api_rows) == len(static_rows)
    api_rows, static_rows = sorted(api_rows, key=_key), sorted(static_rows, key=_key)
    for a, s in zip(api_rows, static_rows, strict=True):
        for k, v in a.items():
            v = _norm(v)
            if v is None and nulls_dropped:
                assert k not in s or s[k] is None, k
            else:
                assert s.get(k) == v, (k, v, s.get(k))


def test_top_level(api):
    _same_rows(api.screen(), _load("screen.json"), nulls_dropped=True)
    _same_rows(api.bdcs(public_only=False), _load("bdcs.json"), nulls_dropped=True)
    pub = [b for b in _load("bdcs.json") if b.get("is_public")]
    assert len(pub) == len(api.bdcs(public_only=True))


def test_bdc_loans_match_api(api):
    rng = random.Random(7)
    files = sorted((DATA / "bdcs").glob("*/loans/*.json"))
    for f in rng.sample(files, 8):
        cik, period = int(f.parts[-3]), f.stem
        d = _load(f.relative_to(DATA).as_posix())
        rows = _expand(d["cols"], d["rows"], d["footnotes"])
        api_rows = api.bdc_loans(cik, period=period, flag="all")
        _same_rows(api_rows, rows, nulls_dropped=False)
        # the client-side "debt" filter equals the server-side one
        assert sorted(r["loan_id"] for r in rows if r["is_debt"]) == sorted(
            r["loan_id"] for r in api.bdc_loans(cik, period=period, flag="debt")
        )


def test_loan_detail_matches_api(api):
    rng = random.Random(11)
    shard_files = sorted((DATA / "loans").glob("*.json"))
    for f in rng.sample(shard_files, 5):
        shard = _load(f"loans/{f.name}")
        loan_id = rng.choice(list(shard["loans"]))
        assert loan_shard(loan_id) == f.stem
        e = shard["loans"][loan_id]
        d = api.loan_detail(loan_id)
        _same_rows([d["loan"]], [e["loan"]], nulls_dropped=True)
        hist = _expand(shard["history_cols"], e["history"], shard["footnotes"])
        _same_rows(d["history"], hist, nulls_dropped=False)
        # peers come from the borrower shard
        key = e["loan"]["borrower_key"]
        bs = _load(f"borrowers/{borrower_shard(key)}.json")
        marks = _expand(bs["mark_cols"], bs["borrowers"][key]["marks"])
        assert len(marks) == len(d["peers"])
        assert {(m["cik"], m["period_end"]) for m in marks} == {
            (p["cik"], _norm(p["period_end"])) for p in d["peers"]
        }


def test_borrower_detail_matches_api(api):
    idx = _load("borrowers/index.json")
    index = _expand(idx["cols"], idx["rows"])
    # search semantics: substring on key or name, index order (n_bdcs desc, n_loans desc)
    q = index[0]["borrower_key"][:3]
    hits = [r for r in index if q in r["borrower_key"].lower() or q in (r.get("issuer_name") or "").lower()]
    api_hits = api.borrowers(q=q, limit=50)
    assert [r["borrower_key"] for r in hits[:50]] == [r["borrower_key"] for r in api_hits]
    rng = random.Random(3)
    for r in rng.sample(index, 6):
        key = r["borrower_key"]
        bs = _load(f"borrowers/{borrower_shard(key)}.json")
        e = bs["borrowers"][key]
        d = api.borrower_detail(key)
        _same_rows(d["loans"], _expand(bs["loan_cols"], e["loans"]), nulls_dropped=False)
        _same_rows(d["marks"], _expand(bs["mark_cols"], e["marks"]), nulls_dropped=False)


def test_manifest_counts():
    m = _load("manifest.json")
    assert m["n_files"] == sum(1 for _ in Path(DATA).rglob("*.json"))
    assert m["loan_shards"] == 4096 and m["borrower_shards"] == 256
