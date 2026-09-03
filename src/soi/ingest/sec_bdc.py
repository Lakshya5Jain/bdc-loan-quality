"""Download SEC BDC bulk data-set zips and load them into DuckDB raw tables."""
from __future__ import annotations

import datetime as dt
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import httpx

from soi.config import BDC_DATASET_BASE, settings

FIRST_QUARTERLY = (2022, 4)
LAST_QUARTERLY = (2025, 4)  # SEC switched to monthly files in 2026
FIRST_MONTHLY = (2026, 1)

FILE_RE = re.compile(r"^(\d{4})(?:q([1-4])|_(\d{2}))_bdc\.zip$")


@dataclass(frozen=True, order=True)
class BulkFile:
    sort_key: tuple[int, int, int]
    name: str

    @property
    def url(self) -> str:
        return BDC_DATASET_BASE + self.name

    @property
    def path(self) -> Path:
        return settings.raw_bdc_dir / self.name


def parse_name(name: str) -> BulkFile | None:
    m = FILE_RE.match(name)
    if not m:
        return None
    year = int(m.group(1))
    if m.group(2):
        q = int(m.group(2))
        return BulkFile((year, q * 3, 0), name)
    month = int(m.group(3))
    return BulkFile((year, month, 1), name)


def candidate_files(today: dt.date | None = None) -> list[BulkFile]:
    """All bulk file names that could exist as of today (quarterly then monthly)."""
    today = today or dt.date.today()
    out: list[BulkFile] = []
    y, q = FIRST_QUARTERLY
    while (y, q) <= LAST_QUARTERLY:
        out.append(parse_name(f"{y}q{q}_bdc.zip"))  # type: ignore[arg-type]
        q += 1
        if q == 5:
            y, q = y + 1, 1
    y, m = FIRST_MONTHLY
    while (y, m) <= (today.year, today.month):
        out.append(parse_name(f"{y}_{m:02d}_bdc.zip"))  # type: ignore[arg-type]
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=httpx.Timeout(120.0, connect=30.0),
        follow_redirects=True,
    )


def remote_exists(client: httpx.Client, f: BulkFile) -> bool:
    r = client.get(f.url, headers={"Range": "bytes=0-0"})
    return r.status_code in (200, 206)


def download(f: BulkFile, client: httpx.Client | None = None, force: bool = False) -> Path | None:
    """Download one bulk zip. Returns the path, or None if it does not exist remotely."""
    settings.ensure_dirs()
    if f.path.exists() and not force:
        return f.path
    own = client is None
    client = client or _client()
    try:
        if not remote_exists(client, f):
            return None
        tmp = f.path.with_suffix(".part")
        with client.stream("GET", f.url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                fh.writelines(r.iter_bytes(1 << 20))
        tmp.rename(f.path)
        return f.path
    finally:
        if own:
            client.close()


# --- loading -------------------------------------------------------------------------------

RAW_TABLES = {
    "sub": "datasets/sub.tsv",
    "num": "datasets/num.tsv",
    "txt": "datasets/txt.tsv",
    "tag": "datasets/tag.tsv",
}

CASTS = {
    "sub": {
        "cik": "BIGINT", "period": "DATE", "fy": "INTEGER", "filed": "DATE",
        "prevrpt": "INTEGER", "detail": "INTEGER", "pubfloatusd": "DOUBLE",
    },
    "num": {
        "ddate": "DATE", "qtrs": "INTEGER", "dimn": "INTEGER", "value": "DOUBLE",
        "footlen": "INTEGER",
    },
    "txt": {"ddate": "DATE", "qtrs": "INTEGER", "dimn": "INTEGER", "txtlen": "INTEGER",
            "footlen": "INTEGER"},
    "tag": {"custom": "INTEGER", "abstract": "INTEGER"},
}


def _read_tsv_sql(path: Path) -> str:
    # SEC TSVs are unquoted; disable quote/escape handling so embedded quotes survive.
    return (
        f"read_csv('{path}', delim='\\t', header=true, quote='', escape='', "
        "all_varchar=true, null_padding=true, ignore_errors=false, strict_mode=false)"
    )


def _target_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='raw' AND table_name=? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def load_zip(con: duckdb.DuckDBPyConnection, zpath: Path, force: bool = False) -> dict[str, int]:
    """Extract a bulk zip to a temp dir and append its tables into raw.*"""
    name = zpath.name
    already = con.execute(
        "SELECT 1 FROM raw.loaded_files WHERE source_file=?", [name]
    ).fetchone()
    if already and not force:
        return {}
    if already:
        for t in RAW_TABLES:
            con.execute(f"DELETE FROM raw.{t} WHERE source_file=?", [name])
        con.execute("DELETE FROM raw.loaded_files WHERE source_file=?", [name])

    counts: dict[str, int] = {}
    tmpdir = Path(tempfile.mkdtemp(prefix="soi_", dir=settings.raw_bdc_dir))
    try:
        with zipfile.ZipFile(zpath) as z:
            for member in RAW_TABLES.values():
                z.extract(member, tmpdir)
        con.execute("BEGIN")
        for table, member in RAW_TABLES.items():
            tsv = tmpdir / member
            src_cols = [
                r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {_read_tsv_sql(tsv)}").fetchall()
            ]
            targets = _target_columns(con, table)
            casts = CASTS[table]
            select_parts = []
            for col in targets:
                if col == "source_file":
                    select_parts.append(f"'{name}' AS source_file")
                elif col in src_cols:
                    expr = f'"{col}"'
                    if col in casts:
                        if casts[col] == "DATE":
                            expr = f"try_strptime(nullif({expr}, ''), '%Y%m%d')::DATE"
                        else:
                            expr = f"try_cast(nullif({expr}, '') AS {casts[col]})"
                    select_parts.append(f"{expr} AS \"{col}\"")
                else:
                    select_parts.append(f"NULL AS \"{col}\"")
            con.execute(
                f"INSERT INTO raw.{table} SELECT {', '.join(select_parts)} "
                f"FROM {_read_tsv_sql(tsv)}"
            )
            counts[table] = con.execute(
                f"SELECT count(*) FROM raw.{table} WHERE source_file=?", [name]
            ).fetchone()[0]
        con.execute(
            "INSERT INTO raw.loaded_files (source_file, n_sub, n_num, n_txt, n_tag) VALUES (?,?,?,?,?)",
            [name, counts["sub"], counts["num"], counts["txt"], counts["tag"]],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return counts


def ingest(
    con: duckdb.DuckDBPyConnection,
    since: str | None = None,
    only: list[str] | None = None,
    force: bool = False,
    log=print,
) -> list[str]:
    """Download (if needed) and load every candidate bulk file. Returns loaded file names."""
    files = candidate_files()
    if only:
        wanted = set(only)
        files = [f for f in files if f.name in wanted]
    if since:
        s = parse_name(since if since.endswith(".zip") else f"{since}_bdc.zip")
        if s is None:
            raise ValueError(f"bad --since value {since!r}; expected e.g. 2025q3 or 2026_03")
        files = [f for f in files if f.sort_key >= s.sort_key]
    loaded: list[str] = []
    with _client() as client:
        for f in files:
            done = con.execute(
                "SELECT 1 FROM raw.loaded_files WHERE source_file=?", [f.name]
            ).fetchone()
            if done and not force:
                log(f"skip {f.name} (already loaded)")
                continue
            path = download(f, client=client)
            if path is None:
                log(f"missing {f.name} (not published yet)")
                continue
            log(f"loading {f.name} ...")
            counts = load_zip(con, path, force=force)
            log(f"  {counts}")
            loaded.append(f.name)
    return loaded
