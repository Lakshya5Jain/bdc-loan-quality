"""DuckDB connection and schema management."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from soi.config import settings

SCHEMAS = ("raw", "ref", "core", "signals", "market")

RAW_DDL = """
CREATE TABLE IF NOT EXISTS raw.loaded_files (
    source_file VARCHAR PRIMARY KEY,
    loaded_at   TIMESTAMP DEFAULT now(),
    n_sub INTEGER, n_num INTEGER, n_txt INTEGER, n_tag INTEGER
);
CREATE TABLE IF NOT EXISTS raw.sub (
    adsh VARCHAR, cik BIGINT, name VARCHAR, countryba VARCHAR, stprba VARCHAR, cityba VARCHAR,
    zipba VARCHAR, bas1 VARCHAR, bas2 VARCHAR, baph VARCHAR, countryma VARCHAR, stprma VARCHAR,
    cityma VARCHAR, zipma VARCHAR, mas1 VARCHAR, mas2 VARCHAR, countryinc VARCHAR, stprinc VARCHAR,
    ein VARCHAR, former VARCHAR, changed VARCHAR, afs VARCHAR, wksi VARCHAR, fye VARCHAR,
    form VARCHAR, period DATE, fy INTEGER, fp VARCHAR, filed DATE, fileNumber VARCHAR,
    accepted VARCHAR, prevrpt INTEGER, detail INTEGER, instance VARCHAR, pubfloatusd DOUBLE,
    floatdate VARCHAR, inlineurl VARCHAR, source_file VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.num (
    adsh VARCHAR, tag VARCHAR, version VARCHAR, ddate DATE, qtrs INTEGER, uom VARCHAR,
    segments VARCHAR, dimn INTEGER, value DOUBLE, footnote VARCHAR, footlen INTEGER,
    source_file VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.txt (
    adsh VARCHAR, tag VARCHAR, version VARCHAR, ddate DATE, qtrs INTEGER, segments VARCHAR,
    dimn INTEGER, value VARCHAR, txtlen INTEGER, footnote VARCHAR, footlen INTEGER,
    source_file VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.tag (
    tag VARCHAR, version VARCHAR, custom INTEGER, abstract INTEGER, datatype VARCHAR,
    iord VARCHAR, crdr VARCHAR, tlabel VARCHAR, doc VARCHAR, source_file VARCHAR
);
"""


def connect(read_only: bool = False, path: Path | None = None) -> duckdb.DuckDBPyConnection:
    settings.ensure_dirs()
    p = path or settings.db_path
    con = duckdb.connect(str(p), read_only=read_only)
    if not read_only:
        for s in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        con.execute(RAW_DDL)
    return con


@contextmanager
def db(read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    con = connect(read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
        [schema, table],
    ).fetchone()
    return bool(row and row[0])


def export_parquet(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> Path:
    out = settings.parquet_dir / f"{schema}.{table}.parquet"
    con.execute(f"COPY {schema}.{table} TO '{out}' (FORMAT PARQUET)")
    return out
