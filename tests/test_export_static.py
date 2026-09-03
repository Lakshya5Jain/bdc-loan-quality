"""Unit tests for the static export encoding (no database needed)."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess

import pytest

from soi.export_static import FootnoteDict, _row, _table, borrower_shard, fnv1a, loan_shard

SAMPLES = ["", "a", "1782524-3174", "generator", "ppva fund", "café – ünïcode ✓", "x" * 300]

# Same function as `fnv1a` in web/src/lib/api.ts, kept verbatim so a drift on either side fails.
JS = r"""
function fnv1a(s) {
  let h = 0x811c9dc5
  for (const b of new TextEncoder().encode(s)) { h ^= b; h = Math.imul(h, 0x01000193) >>> 0 }
  return h >>> 0
}
const xs = JSON.parse(process.argv[1])
console.log(JSON.stringify(xs.map(fnv1a)))
"""


def test_fnv1a_known_vectors():
    assert fnv1a("") == 0x811C9DC5
    assert fnv1a("a") == 0xE40C292C
    assert fnv1a("foobar") == 0xBF9CF968


def test_shards_are_zero_padded_hex():
    assert len(loan_shard("1782524-3174")) == 3
    assert len(borrower_shard("generator")) == 2
    assert all(ch in "0123456789abcdef" for ch in loan_shard("x") + borrower_shard("y"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_fnv1a_matches_javascript():
    out = subprocess.run(
        ["node", "-e", JS, json.dumps(SAMPLES)], capture_output=True, text=True, check=True
    ).stdout
    assert json.loads(out) == [fnv1a(s) for s in SAMPLES]


def test_row_drops_nulls_and_normalises():
    r = _row({"a": None, "b": float("nan"), "c": 0.1 + 0.2, "d": dt.date(2026, 3, 31), "e": True})
    assert r == {"c": 0.3, "d": "2026-03-31", "e": True}


def test_table_footnote_dictionary():
    fnd = FootnoteDict()
    rows = [
        {"id": 1, "footnote_text": "long boilerplate"},
        {"id": 2, "footnote_text": None},
        {"id": 3, "footnote_text": "long boilerplate"},
        {"id": 4, "footnote_text": "other"},
    ]
    t = _table(rows, ["id", "footnote_text"], fnd)
    assert t["cols"] == ["id", "fn"]
    assert t["rows"] == [[1, 0], [2, None], [3, 0], [4, 1]]
    assert fnd.notes == ["long boilerplate", "other"]
    # without a dictionary the text passes through untouched
    assert _table(rows[:1], ["id", "footnote_text"])["rows"] == [[1, "long boilerplate"]]
