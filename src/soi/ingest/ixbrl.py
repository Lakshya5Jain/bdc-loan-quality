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


# ---- fact extraction (filings whose bulk data misses rows) -----------------------------------------

XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2011": "-", "\u00a0": " "})


def _norm_ident(text: str) -> str:
    """The bulk data set writes en/em dashes as hyphens; do the same so identifiers match."""
    return re.sub(r"\s+", " ", text.translate(_DASHES)).strip()


def _parse_number(text: str, fmt: str, scale: str | None, sign: str | None) -> float | None:
    t = text.strip()
    fmt = (fmt or "").rsplit(":", 1)[-1]
    if fmt in ("fixed-zero", "zerodash", "numdash", "fixed-empty") or t in ("", "-", "\u2014", "\u2013"):
        return 0.0
    if fmt == "num-comma-decimal":
        t = t.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        t = t.replace(",", "").replace(" ", "")
    t = re.sub(r"[^0-9.]", "", t)
    if not t or t == ".":
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    if scale:
        v *= 10 ** int(scale)
    return -v if sign == "-" else v


def parse_facts(path: Path, adsh: str = "", txt_tags: frozenset[str] = frozenset()) -> list[tuple]:
    """(period_end, identifier, tag, value, uom, txt) for every fact in an identifier context:
    numeric facts (all tags) and the text facts named in txt_tags."""
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.parse(str(path), parser).getroot()
    ctx_ident: dict[str, tuple[str, str]] = {}
    for ctx in root.iter(f"{{{XBRLI_NS}}}context"):
        inst = ctx.find(f".//{{{XBRLI_NS}}}instant")
        end = ctx.find(f".//{{{XBRLI_NS}}}endDate")
        pend = (inst.text if inst is not None else end.text if end is not None else "").strip()
        for tm in ctx.iter(f"{{{XBRLDI_NS}}}typedMember"):
            if tm.get("dimension", "").endswith(IDENT_AXIS):
                ctx_ident[ctx.get("id")] = (pend, _norm_ident(_text(tm)))
    units: dict[str, str] = {}
    for u in root.iter(f"{{{XBRLI_NS}}}unit"):
        m = u.find(f".//{{{XBRLI_NS}}}measure")
        if m is not None and m.text:
            units[u.get("id")] = m.text.rsplit(":", 1)[-1]
    rows: list[tuple] = []
    for f in root.iter(f"{{{IX_NS}}}nonFraction"):
        key = ctx_ident.get(f.get("contextRef", ""))
        if not key or f.get(XSI_NIL) == "true":
            continue
        v = _parse_number(_text(f), f.get("format", ""), f.get("scale"), f.get("sign"))
        if v is None:
            continue
        tag = f.get("name", "").rsplit(":", 1)[-1]
        rows.append((key[0], key[1], tag, v, units.get(f.get("unitRef", ""), ""), None))
    if txt_tags:
        for f in root.iter(f"{{{IX_NS}}}nonNumeric"):
            tag = f.get("name", "").rsplit(":", 1)[-1]
            if tag not in txt_tags:
                continue
            key = ctx_ident.get(f.get("contextRef", ""))
            if not key or f.get(XSI_NIL) == "true":
                continue
            rows.append((key[0], key[1], tag, None, "", _text(f)))
    return rows


FACTS_DDL = """
CREATE TABLE IF NOT EXISTS raw.ix_facts (
    adsh VARCHAR, ddate DATE, identifier VARCHAR, tag VARCHAR, value DOUBLE, uom VARCHAR, txt VARCHAR
);
CREATE TABLE IF NOT EXISTS raw.ix_fact_filings (
    adsh VARCHAR PRIMARY KEY, n_facts INTEGER, error VARCHAR, parsed_at TIMESTAMP DEFAULT now()
);
"""


def gap_filings(con, lo: float = 0.5, hi: float = 0.97, public_only: bool = True) -> list[tuple]:
    """Chosen filings whose tagged detail covers lo..hi of the reported total: candidates for
    reading the facts from the filing itself."""
    where = ["f.inlineurl IS NOT NULL", f"r.coverage BETWEEN {lo} AND {hi}", "r.override_note IS NULL"]
    if public_only:
        where.append("m.is_public")
    return con.execute(
        f"""
        SELECT f.adsh, f.inlineurl, m.ticker, f.period
        FROM core.reconciliation r JOIN core.filings f USING (adsh)
        JOIN ref.bdc_master m ON m.cik = r.cik
        WHERE {' AND '.join(where)}
        GROUP BY ALL ORDER BY f.period DESC, m.ticker
        """
    ).fetchall()


def build_ix_facts(con, todo: list[tuple], keep_html: bool = False, log=print, force: bool = False) -> str:
    """Download the given filings and store every fact of their identifier contexts in raw.ix_facts."""
    from soi.transform.holdings import TXT_TAGS

    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(FACTS_DDL)
    if not force:
        done = {r[0] for r in con.execute("SELECT adsh FROM raw.ix_fact_filings WHERE error IS NULL").fetchall()}
        todo = [t for t in todo if t[0] not in done]
    log(f"ix facts: {len(todo)} filings to fetch")
    client = httpx.Client(headers={"User-Agent": settings.sec_user_agent},
                          timeout=httpx.Timeout(180.0, connect=30.0), follow_redirects=True)
    n_done = 0
    try:
        for adsh, url, ticker, period in todo:
            in_tx = False
            try:
                path = download(adsh, url, client)
                rows = [(adsh, *r) for r in parse_facts(path, adsh, frozenset(TXT_TAGS))]
                con.execute("BEGIN")
                in_tx = True
                con.execute("DELETE FROM raw.ix_facts WHERE adsh = ?", [adsh])
                if rows:
                    con.executemany(
                        "INSERT INTO raw.ix_facts VALUES (?, try_cast(? AS DATE), ?, ?, ?, ?, ?)", rows
                    )
                con.execute("DELETE FROM raw.ix_fact_filings WHERE adsh = ?", [adsh])
                con.execute("INSERT INTO raw.ix_fact_filings (adsh, n_facts, error) VALUES (?, ?, NULL)",
                            [adsh, len(rows)])
                con.execute("COMMIT")
                n_done += 1
                log(f"  {ticker or ''} {period} {adsh}: {len(rows)} facts")
                if not keep_html:
                    path.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                if in_tx:
                    con.execute("ROLLBACK")
                con.execute("DELETE FROM raw.ix_fact_filings WHERE adsh = ?", [adsh])
                con.execute("INSERT INTO raw.ix_fact_filings (adsh, n_facts, error) VALUES (?, 0, ?)",
                            [adsh, str(e)[:500]])
                log(f"  FAILED {ticker or ''} {period} {adsh}: {e}")
    finally:
        client.close()
    n = con.execute("SELECT count(*), count(DISTINCT adsh) FROM raw.ix_facts").fetchone()
    return f"raw.ix_facts: {n[0]:,} facts across {n[1]} filings ({n_done} parsed this run)"


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
            in_tx = False
            try:
                path = download(adsh, url, client)
                res = parse_footnotes(path, adsh)
                rows = [(adsh, pend, ident, fn) for (pend, ident), fns in res.by_identifier.items()
                        for fn in fns if pend]
                con.execute("BEGIN")
                in_tx = True
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
                if in_tx:
                    con.execute("ROLLBACK")
                con.execute("DELETE FROM raw.ix_filings WHERE adsh = ?", [adsh])
                con.execute(
                    "INSERT INTO raw.ix_filings (adsh, n_rows, error) VALUES (?, 0, ?)", [adsh, str(e)[:500]]
                )
                log(f"  FAILED {ticker or ''} {period} {adsh}: {e}")
    finally:
        client.close()
    n = con.execute("SELECT count(*), count(DISTINCT adsh) FROM raw.ix_footnotes").fetchone()
    return f"raw.ix_footnotes: {n[0]:,} links across {n[1]} filings ({done} parsed this run)"
