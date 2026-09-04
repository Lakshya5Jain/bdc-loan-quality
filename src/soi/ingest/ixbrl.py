"""Footnote enrichment from the inline-XBRL filing itself.

The SEC bulk data attaches each footnote to a single fact, but filers link one footnote (for
example "Loan was on non-accrual status") to every fact of every affected holding via
ix:relationship elements. Parsing the filing recovers the full mapping identifier -> footnotes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from lxml import etree

from soi.config import settings

IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
IDENT_AXIS = "InvestmentIdentifierAxis"


@dataclass
class FilingFootnotes:
    adsh: str
    n_footnotes: int = 0
    n_relationships: int = 0
    n_contexts: int = 0
    # (period_end, identifier text) -> list of footnote texts
    by_identifier: dict[tuple[str, str], list[str]] = field(default_factory=dict)


def archive_url(inlineurl: str) -> str:
    """https://www.sec.gov/ix?doc=/Archives/... -> https://www.sec.gov/Archives/..."""
    m = re.search(r"doc=(/Archives/[^&]+)", inlineurl)
    return "https://www.sec.gov" + m.group(1) if m else inlineurl


def local_path(adsh: str, inlineurl: str) -> Path:
    name = archive_url(inlineurl).rsplit("/", 1)[-1]
    return settings.data_dir / "raw" / "filings" / f"{adsh}_{name}"


def download(adsh: str, inlineurl: str, client: httpx.Client | None = None) -> Path:
    path = local_path(adsh, inlineurl)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": settings.sec_user_agent},
                                    timeout=httpx.Timeout(180.0, connect=30.0), follow_redirects=True)
    try:
        with client.stream("GET", archive_url(inlineurl)) as r:
            r.raise_for_status()
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as fh:
                fh.writelines(r.iter_bytes(1 << 20))
            tmp.rename(path)
    finally:
        if own:
            client.close()
    return path


def _text(el: etree._Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def parse_footnotes(path: Path, adsh: str = "") -> FilingFootnotes:
    """Map each InvestmentIdentifierAxis member text to the footnotes linked to its facts."""
    parser = etree.XMLParser(recover=True, huge_tree=True)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()
    out = FilingFootnotes(adsh=adsh)

    # contexts: id -> (period end, identifier member text)
    ctx_ident: dict[str, tuple[str, str]] = {}
    for ctx in root.iter(f"{{{XBRLI_NS}}}context"):
        cid = ctx.get("id")
        inst = ctx.find(f".//{{{XBRLI_NS}}}instant")
        end = ctx.find(f".//{{{XBRLI_NS}}}endDate")
        pend = (inst.text if inst is not None else end.text if end is not None else "").strip()
        for tm in ctx.iter(f"{{{XBRLDI_NS}}}typedMember"):
            dim = tm.get("dimension", "")
            if dim.endswith(IDENT_AXIS):
                ctx_ident[cid] = (pend, _text(tm))
    out.n_contexts = len(ctx_ident)

    # facts: id -> contextRef (nonFraction and nonNumeric)
    fact_ctx: dict[str, str] = {}
    for tag in ("nonFraction", "nonNumeric"):
        for f in root.iter(f"{{{IX_NS}}}{tag}"):
            fid = f.get("id")
            if fid:
                fact_ctx[fid] = f.get("contextRef", "")

    # footnotes: id -> text
    fn_text: dict[str, str] = {}
    for fn in root.iter(f"{{{IX_NS}}}footnote"):
        fid = fn.get("id")
        if fid:
            fn_text[fid] = _text(fn)
    out.n_footnotes = len(fn_text)

    # relationships: fromRefs (facts) -> toRefs (footnotes)
    for rel in root.iter(f"{{{IX_NS}}}relationship"):
        out.n_relationships += 1
        froms = (rel.get("fromRefs") or "").split()
        tos = (rel.get("toRefs") or "").split()
        texts = [fn_text[t] for t in tos if t in fn_text]
        if not texts:
            continue
        for fr in froms:
            key = ctx_ident.get(fact_ctx.get(fr, ""))
            if not key:
                continue
            lst = out.by_identifier.setdefault(key, [])
            for t in texts:
                if t not in lst:
                    lst.append(t)
    return out


# ---- batch enrichment ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS raw.ix_footnotes (
    adsh VARCHAR, period_end DATE, identifier VARCHAR, footnote VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.ix_filings (
    adsh VARCHAR PRIMARY KEY, n_contexts INTEGER, n_footnotes INTEGER, n_relationships INTEGER,
    n_rows INTEGER, error VARCHAR, parsed_at TIMESTAMP DEFAULT now()
);
"""


def filings_todo(con, public_only: bool = True, since: str | None = None) -> list[tuple]:
    """(adsh, inlineurl, ticker, period) for every chosen filing not yet parsed."""
    where = ["f.inlineurl IS NOT NULL"]
    has_ix = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='raw' AND table_name='ix_filings'"
    ).fetchone()[0]
    if has_ix:
        where.append("f.adsh NOT IN (SELECT adsh FROM raw.ix_filings WHERE error IS NULL)")
    if public_only:
        where.append("m.is_public")
    if since:
        where.append(f"f.period >= DATE '{since}'")
    sql = f"""
        SELECT f.adsh, f.inlineurl, m.ticker, f.period
        FROM core.period_source ps
        JOIN core.filings f USING (adsh)
        JOIN ref.bdc_master m ON m.cik = ps.cik
        WHERE {' AND '.join(where)}
        GROUP BY ALL ORDER BY f.period DESC, m.ticker
    """
    return con.execute(sql).fetchall()


def build_ix_footnotes(con, public_only: bool = True, since: str | None = None,
                       keep_html: bool = False, limit: int | None = None, log=print,
                       todo: list[tuple] | None = None) -> str:
    """Download every selected filing, parse its footnote links, store them in raw.ix_footnotes.
    HTML files are deleted after parsing unless keep_html (they are 10-25 MB each).
    `todo` may be supplied (from filings_todo on another database) so that the writes can go to a
    separate DuckDB file."""
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(DDL)
    if todo is None:
        todo = filings_todo(con, public_only=public_only, since=since)
    done_adsh = {r[0] for r in con.execute("SELECT adsh FROM raw.ix_filings WHERE error IS NULL").fetchall()}
    todo = [t for t in todo if t[0] not in done_adsh]
    if limit:
        todo = todo[:limit]
    log(f"ix footnotes: {len(todo)} filings to fetch")
    client = httpx.Client(headers={"User-Agent": settings.sec_user_agent},
                          timeout=httpx.Timeout(180.0, connect=30.0), follow_redirects=True)
    done = 0
    try:
        for adsh, url, ticker, period in todo:
            try:
                path = download(adsh, url, client)
                res = parse_footnotes(path, adsh)
                rows = [(adsh, pend, ident, fn) for (pend, ident), fns in res.by_identifier.items()
                        for fn in fns if pend]
                con.execute("BEGIN")
                con.execute("DELETE FROM raw.ix_footnotes WHERE adsh = ?", [adsh])
                if rows:
                    con.executemany(
                        "INSERT INTO raw.ix_footnotes VALUES (?, try_cast(? AS DATE), ?, ?)", rows
                    )
                con.execute("DELETE FROM raw.ix_filings WHERE adsh = ?", [adsh])
                con.execute(
                    "INSERT INTO raw.ix_filings (adsh, n_contexts, n_footnotes, n_relationships, n_rows, error) "
                    "VALUES (?, ?, ?, ?, ?, NULL)",
                    [adsh, res.n_contexts, res.n_footnotes, res.n_relationships, len(rows)],
                )
                con.execute("COMMIT")
                done += 1
                log(f"  {ticker or ''} {period} {adsh}: {len(rows)} footnote links")
                if not keep_html and not path.name.startswith(("arcc-", "main-", "obdc-")):
                    path.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                con.execute("ROLLBACK") if con.execute("SELECT 1").fetchone() else None
                con.execute("DELETE FROM raw.ix_filings WHERE adsh = ?", [adsh])
                con.execute(
                    "INSERT INTO raw.ix_filings (adsh, n_rows, error) VALUES (?, 0, ?)", [adsh, str(e)[:500]]
                )
                log(f"  FAILED {ticker or ''} {period} {adsh}: {e}")
    finally:
        client.close()
    n = con.execute("SELECT count(*), count(DISTINCT adsh) FROM raw.ix_footnotes").fetchone()
    return f"raw.ix_footnotes: {n[0]:,} links across {n[1]} filings ({done} parsed this run)"
