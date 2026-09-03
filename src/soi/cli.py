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


@build_app.command("screen")
def build_screen():
    from soi.db import db
    from soi.signals.market import build_screen as _b

    with db() as con:
        typer.echo(_b(con))


@build_app.command("all")
def build_all():
    from soi.db import db
    from soi.signals.bdc_rollup import build_signals
    from soi.signals.market import build_screen
    from soi.transform.holdings import build_holdings
    from soi.transform.link_loans import build_loans

    with db() as con:
        for step in (build_holdings, build_loans, build_signals, build_screen):
            typer.echo(step(con))


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


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = True):
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
