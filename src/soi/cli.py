"""Command-line entry point: `uv run soi ...`"""
from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, help="BDC loan-quality tracker")
build_app = typer.Typer(no_args_is_help=True, help="Build derived tables")
app.add_typer(build_app, name="build")


@app.command()
def ingest(
    all_files: bool = typer.Option(False, "--all", help="Download and load every bulk file"),
    since: str | None = typer.Option(None, help="Only files at/after this name, e.g. 2025q3"),
    only: list[str] | None = typer.Option(None, help="Specific file names, e.g. 2026_07_bdc.zip"),
    force: bool = typer.Option(False, help="Reload files even if already loaded"),
):
    """Download SEC BDC bulk zips and load raw tables, then refresh reference data."""
    from soi.db import db
    from soi.ingest.reference import build_reference
    from soi.ingest.sec_bdc import ingest as _ingest

    if not (all_files or since or only):
        raise typer.BadParameter("pass --all, --since, or --only")
    with db() as con:
        loaded = _ingest(con, since=since, only=only, force=force, log=typer.echo)
        n = build_reference(con)
        typer.echo(f"loaded {len(loaded)} file(s); ref.bdc_master has {n} BDCs")


@app.command()
def reference(force: bool = typer.Option(False, help="Re-download SEC reference files")):
    """Rebuild ref.bdc_master (BDC list + tickers)."""
    from soi.db import db
    from soi.ingest.reference import build_reference

    with db() as con:
        typer.echo(f"ref.bdc_master: {build_reference(con, force_download=force)} rows")


@build_app.command("holdings")
def build_holdings():
    from soi.db import db
    from soi.transform.holdings import build_holdings as _b

    with db() as con:
        typer.echo(_b(con))


@build_app.command("loans")
def build_loans():
    from soi.db import db
    from soi.transform.link_loans import build_loans as _b

    with db() as con:
        typer.echo(_b(con))


@build_app.command("signals")
def build_signals():
    from soi.db import db
    from soi.signals.bdc_rollup import build_signals as _b

    with db() as con:
        typer.echo(_b(con))


@build_app.command("fundamentals")
def build_fundamentals_cmd():
    """Income statement and balance sheet metrics per BDC-quarter (NII, coverage, leverage)."""
    from soi.db import db
    from soi.signals.fundamentals import build_fundamentals

    with db() as con:
        typer.echo(build_fundamentals(con))


@build_app.command("screen")
def build_screen():
    from soi.db import db
    from soi.signals.market import build_screen as _b

    with db() as con:
        typer.echo(_b(con))


@build_app.command("insights")
def build_insights_cmd():
    from soi.db import db
    from soi.signals.insights import build_insights as _b

    with db() as con:
        typer.echo(_b(con))


@build_app.command("stale")
def build_stale_cmd():
    """Stale-mark detector: lender disagreement on shared borrowers, generosity, and its test."""
    from soi.db import db
    from soi.signals.stale_marks import build_stale_marks

    with db() as con:
        typer.echo(build_stale_marks(con, log=typer.echo))


@build_app.command("all")
def build_all():
    from soi.db import db
    from soi.signals.bdc_rollup import build_signals
    from soi.signals.fundamentals import build_fundamentals
    from soi.signals.insights import build_insights
    from soi.signals.market import build_screen
    from soi.signals.stale_marks import build_stale_marks
    from soi.transform.holdings import build_holdings
    from soi.transform.link_loans import build_loans

    with db() as con:
        for step in (build_holdings, build_loans, build_signals, build_fundamentals, build_screen, build_insights,
                     build_stale_marks):
            typer.echo(step(con))


@app.command()
def backtest(event: bool = typer.Option(True, help="Also run the filing-day (event) variant")):
    """Walk-forward backtest of every BDC-level signal on public BDC stock returns."""
    from soi.db import db
    from soi.signals.backtest import run_backtest, run_event_backtest

    with db() as con:
        typer.echo(run_backtest(con, log=typer.echo))
        if event:
            typer.echo(run_event_backtest(con, log=typer.echo))


@app.command()
def prices(
    tickers: list[str] | None = typer.Option(None, help="Limit to these tickers"),
    start: str = typer.Option("2021-01-01", help="History start date"),
):
    """Fetch daily prices/dividends for public BDCs via yfinance and compute NAV table."""
    from soi.db import db
    from soi.ingest.prices import fetch_prices

    with db() as con:
        typer.echo(fetch_prices(con, tickers=tickers, start=start))


@app.command("ixfootnotes")
def ixfootnotes(
    all_bdcs: bool = typer.Option(False, "--all", help="All BDCs, not only public ones"),
    since: str | None = typer.Option(None, help="Only filings with period >= this date"),
    limit: int | None = typer.Option(None, help="Stop after N filings"),
    keep_html: bool = typer.Option(False, help="Keep downloaded filings under data/raw/filings"),
):
    """Download filings and recover footnote links (non-accrual, PIK, affiliation) per holding."""
    from soi.db import db
    from soi.ingest.ixbrl import build_ix_footnotes

    with db() as con:
        typer.echo(build_ix_footnotes(con, public_only=not all_bdcs, since=since,
                                      keep_html=keep_html, limit=limit, log=typer.echo))


@app.command("ixfacts")
def ixfacts(
    gaps: bool = typer.Option(True, help="Filings whose tagged detail covers 50-97% of the reported total"),
    ticker: list[str] | None = typer.Option(None, help="Only these tickers"),
    all_bdcs: bool = typer.Option(False, "--all", help="All BDCs, not only public ones"),
    force: bool = typer.Option(False, help="Re-parse filings already done"),
):
    """Read holdings facts from the filing itself where the SEC bulk data misses rows (GSBD)."""
    from soi.db import db
    from soi.ingest.ixbrl import build_ix_facts, gap_filings

    with db() as con:
        todo = gap_filings(con, public_only=not all_bdcs) if gaps else []
        if ticker:
            todo = [t for t in todo if t[2] in ticker]
        typer.echo(build_ix_facts(con, todo, log=typer.echo, force=force))


@app.command("export-static")
def export_static(
    out: str = typer.Option("web/public/data", help="Output directory (wiped first)"),
):
    """Write every API response as static JSON so the web app can be hosted without a server."""
    from pathlib import Path

    from soi.config import PROJECT_ROOT
    from soi.export_static import export_static as _export

    dest = Path(out)
    if not dest.is_absolute():
        dest = PROJECT_ROOT / dest
    _export(dest, log=typer.echo)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = True):
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
