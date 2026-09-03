"""Shared read-only DuckDB connection for the API."""
from __future__ import annotations

import math
import threading
from typing import Any

import duckdb

from soi.config import settings

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None


def _base() -> duckdb.DuckDBPyConnection:
    global _con
    with _lock:
        if _con is None:
            _con = duckdb.connect(str(settings.db_path), read_only=True)
        return _con


def cursor() -> duckdb.DuckDBPyConnection:
    """A per-request cursor (DuckDB connections are not thread-safe)."""
    return _base().cursor()


def rows(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = cursor()
    try:
        res = cur.execute(sql, params or [])
        cols = [d[0] for d in res.description]
        out = []
        for r in res.fetchall():
            d = dict(zip(cols, r))
            for k, v in d.items():
                if isinstance(v, float) and math.isnan(v):  # NaN -> null
                    d[k] = None
            out.append(d)
        return out
    finally:
        cur.close()


def one(sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    r = rows(sql, params)
    return r[0] if r else None
