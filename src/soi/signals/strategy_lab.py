"""Strategy lab: variants of the default long/short, tested on the same filing-day universe.

Everything here is additive. The default strategy (`build_default_strategy`) and its tables are
untouched; the lab re-reads `signals.bt_event_universe` and writes `signals.lab_*`.

What is tested
  floors       the same health score, but the book is taken only among names whose median daily
               dollar volume at entry clears $100k / $1m / $2m / $5m. Ranks are still computed
               against every liquid name (the score means the same thing); only the tradable set
               changes. Borrow cost is charged on the short leg by liquidity tier.
  residual     each quarter the health score is regressed on price / NAV across names and the
               residual is ranked instead. If the residual still works the edge is information
               the price does not carry; if it dies, the strategy was a value tilt.
  conviction   positions weighted by how far the score sits from the middle, instead of equal.
  extra inputs generosity on shared borrowers (from the stale-mark tables) and two roll-ups of
               the loan warning score fitted walk-forward (weights from loan-quarters whose
               one-year outcome was already known on the scoring date): the cost-weighted average
               score and the share of cost scored 30 or more. Each is tested alone and as a fifth
               input to the health score.
  turnover     share of each side's book replaced quarter to quarter.

Costs: 40 bp round trip on each leg per holding period (twice the single charge used in the
existing signal table) plus stock borrow on the short leg at 15% / 5% / 1% a year for names
trading under $1m / $1m-$5m / over $5m a day, pro-rated to the holding period.

Tables: signals.lab_risk_wf (walk-forward loan score roll-up per BDC-quarter), signals.lab_universe
(bt_event_universe plus the extra inputs and their peer percentiles), signals.lab_variant_periods,
signals.lab_variant_summary, signals.lab_signal_periods, signals.lab_signal_summary,
signals.lab_book_largecap (today's book at the $5m floor), signals.lab_book_5 (today's ranking on
the five-input score).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import timedelta

import duckdb
import polars as pl

from soi.signals.backtest import (
    DEFAULT_SIDE_FRACTION,
    MIN_NAMES,
    QTR_SQL,
    ROUND_TRIP_COST,
    _health,
    _quintile_spread,
    _spearman,
    _tstat,
)
from soi.signals.insights import BAD_WINDOW_DAYS, FEATURES

FLOORS = [100_000, 1_000_000, 2_000_000, 5_000_000]
BORROW_TIERS = [(1_000_000, 0.15), (5_000_000, 0.05), (float("inf"), 0.01)]  # (< dollar_vol, annual rate)
RISK_HI = 30.0          # loan score at or above this counts as "predicted stress"
MIN_TRAIN = 5_000       # loan-quarters with a known outcome before a walk-forward fit is used
EXTRA_SIGNALS = {       # name: higher is worse?
    "generosity": True,
    "wavg_risk_wf": True,
    "pct_risk_hi_wf": True,
}


def borrow_rate(dollar_vol: float | None) -> float:
    v = dollar_vol or 0.0
    for cap, rate in BORROW_TIERS:
        if v < cap:
            return rate
    return BORROW_TIERS[-1][1]


# ---- walk-forward loan warning score -----------------------------------------------------------


def _feature_columns(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """FEATURE name -> column in signals.loan_features (a few got a _1 suffix because the base
    frame already had a column of that name)."""
    cols = {c[0] for c in con.execute("DESCRIBE signals.loan_features").fetchall()}
    out = {}
    for name, _, _ in FEATURES:
        if f"{name}_1" in cols:
            out[name] = f"{name}_1"
        elif name in cols:
            out[name] = name
    return out


def build_risk_wf(con: duckdb.DuckDBPyConnection, log=print) -> None:
    fcols = _feature_columns(con)
    df = con.execute(
        f"""
        SELECT loan_id, cik, period_end, {QTR_SQL.format(col="period_end")} AS qtr,
               cost, mark, nonaccrual_flag, data_ok, bad_4q,
               {", ".join(f"{c} AS {n}" for n, c in fcols.items())}
        FROM signals.loan_features
        WHERE cost > 0 AND mark IS NOT NULL
        """
    ).pl()
    names = list(fcols)
    quarters = sorted(q for q in df["qtr"].unique().to_list() if q is not None)
    rollups: list[pl.DataFrame] = []
    fit_rows: list[tuple] = []
    for T in quarters:
        cutoff = T - timedelta(days=BAD_WINDOW_DAYS)
        train = df.filter((pl.col("period_end") <= cutoff) & pl.col("bad_4q").is_not_null())
        if train.height < MIN_TRAIN:
            continue
        base = float(train["bad_4q"].cast(pl.Float64).mean())
        if not 0 < base < 1:
            continue
        base_logit = math.log(base / (1 - base))
        weights: dict[str, float] = {}
        for n in names:
            sub = train.filter(pl.col(n))
            k = sub.height
            hit = float(sub["bad_4q"].cast(pl.Float64).mean()) if k else 0.0
            lift = (hit / base) if k >= 30 else 1.0
            w = max(0.0, min(3.0, math.log(lift))) if lift > 0 else 0.0
            weights[n] = w
            fit_rows.append((T, n, k, hit, base, w))
        expr = pl.lit(base_logit)
        for n, w in weights.items():
            if w > 0:
                expr = expr + pl.col(n).cast(pl.Float64) * w
        scored = (
            df.filter((pl.col("qtr") == T) & pl.col("data_ok"))
            .with_columns(
                pl.when(pl.col("nonaccrual_flag") | (pl.col("mark") < 0.80))
                .then(100.0)
                .otherwise(100.0 / (1.0 + (-expr).exp()))
                .alias("score")
            )
            .group_by(["cik", "period_end"])
            .agg(
                (pl.col("score") * pl.col("cost")).sum().alias("num"),
                pl.col("cost").sum().alias("den"),
                pl.col("cost").filter(pl.col("score") >= RISK_HI).sum().alias("hi"),
                pl.len().alias("n_loans"),
            )
            .with_columns(
                (pl.col("num") / pl.col("den")).alias("wavg_risk_wf"),
                (pl.col("hi") / pl.col("den")).alias("pct_risk_hi_wf"),
                pl.lit(T).alias("fit_qtr"),
                pl.lit(train.height).alias("n_train"),
            )
            .select(["cik", "period_end", "wavg_risk_wf", "pct_risk_hi_wf", "n_loans", "fit_qtr", "n_train"])
        )
        rollups.append(scored)
    if rollups:
        con.register("lab_risk_out", pl.concat(rollups))
        con.execute("CREATE OR REPLACE TABLE signals.lab_risk_wf AS SELECT * FROM lab_risk_out")
        con.unregister("lab_risk_out")
    else:
        con.execute(
            "CREATE OR REPLACE TABLE signals.lab_risk_wf (cik BIGINT, period_end DATE, wavg_risk_wf DOUBLE, "
            "pct_risk_hi_wf DOUBLE, n_loans BIGINT, fit_qtr DATE, n_train BIGINT)"
        )
    con.execute(
        "CREATE OR REPLACE TABLE signals.lab_risk_fits (fit_qtr DATE, feature VARCHAR, n INTEGER, hit_rate DOUBLE, "
        "base_rate DOUBLE, weight DOUBLE)"
    )
    con.executemany("INSERT INTO signals.lab_risk_fits VALUES (?,?,?,?,?,?)", fit_rows)
    log(f"  walk-forward loan score: {len(rollups)} scoring quarters, first fit {rollups[0]['fit_qtr'][0] if rollups else None}")


# ---- universe with the extra inputs ------------------------------------------------------------


def build_lab_universe(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.lab_universe AS
        SELECT u.*,
               CASE WHEN s.n_shared >= 5 THEN s.generosity END AS generosity,
               r.wavg_risk_wf, r.pct_risk_hi_wf
        FROM signals.bt_event_universe u
        LEFT JOIN signals.stale_bdc s USING (cik, period_end)
        LEFT JOIN signals.lab_risk_wf r USING (cik, period_end)
        """
    )
    # peer percentile as of the entry date, same definition as the event backtest
    for s in EXTRA_SIGNALS:
        con.execute(
            f"""
            ALTER TABLE signals.lab_universe ADD COLUMN IF NOT EXISTS {s}_pct DOUBLE;
            UPDATE signals.lab_universe u SET {s}_pct = (
                SELECT avg((o.{s} < u.{s})::INT) + 0.5 * avg((o.{s} = u.{s})::INT)
                FROM (
                    SELECT v.cik, arg_max(v.{s}, v.period_end) AS {s}
                    FROM signals.lab_universe v
                    WHERE v.cik <> u.cik AND v.filed <= u.entry_date AND v.{s} IS NOT NULL
                      AND v.period_end >= u.period_end - INTERVAL 200 DAY
                    GROUP BY v.cik
                ) o
            ) WHERE u.{s} IS NOT NULL
            """
        )


# ---- scorers -----------------------------------------------------------------------------------

Scorer = Callable[[list[dict]], dict[int, float | None]]


def _health_plus(row: dict, extra: str | None) -> float | None:
    """Default four-input health, optionally with a fifth oriented input (higher = worse)."""
    vals = []
    for s in ("pct_debt_below_90", "pct_debt_below_95", "debt_mark", "nav_chg_4q"):
        v = row.get(f"{s}_pct")
        if v is None:
            continue
        vals.append(v if s in ("debt_mark", "nav_chg_4q") else 1.0 - v)
    if extra is not None:
        v = row.get(f"{extra}_pct")
        if v is not None:
            vals.append(1.0 - v)
    return sum(vals) / len(vals) if len(vals) >= 2 else None


def scorer_default(rows: list[dict]) -> dict[int, float | None]:
    return {r["cik"]: _health(r) for r in rows}


def scorer_plus(extra: str) -> Scorer:
    return lambda rows: {r["cik"]: _health_plus(r, extra) for r in rows}


def scorer_residual(base: Scorer) -> Scorer:
    """Cross-sectional OLS of the base score on price / NAV; the residual is the score."""

    def f(rows: list[dict]) -> dict[int, float | None]:
        h = base(rows)
        pts = [(r["p_nav"], h[r["cik"]], r["cik"]) for r in rows
               if r.get("p_nav") is not None and 0.2 < r["p_nav"] < 3 and h.get(r["cik"]) is not None]
        if len(pts) < 8:
            return {r["cik"]: None for r in rows}
        mx = sum(x for x, _, _ in pts) / len(pts)
        my = sum(y for _, y, _ in pts) / len(pts)
        sxx = sum((x - mx) ** 2 for x, _, _ in pts)
        b = sum((x - mx) * (y - my) for x, y, _ in pts) / sxx if sxx > 0 else 0.0
        a = my - b * mx
        out = {r["cik"]: None for r in rows}
        for x, y, cik in pts:
            out[cik] = y - (a + b * x)
        return out

    return f


# ---- the book engine ---------------------------------------------------------------------------


def _weights(side: list[tuple], k: int, mid: float, conviction: bool) -> list[float]:
    """Equal dollars, or dollars in proportion to how far each score sits from the middle name."""
    if not conviction:
        return [1.0 / k] * k
    raw = [abs(s - mid) + 1e-6 for s, _ in side]
    tot = sum(raw)
    return [w / tot for w in raw]


def run_variant(name: str, by_q: dict, scorer: Scorer, floor: float, conviction: bool) -> list[tuple]:
    out: list[tuple] = []
    prev_long: set[str] = set()
    prev_short: set[str] = set()
    for qtr, rs in sorted(by_q.items()):
        scores = scorer(rs)
        cand = [(scores[r["cik"]], r) for r in rs
                if scores.get(r["cik"]) is not None and r["excess_ret"] is not None
                and (r["dollar_vol"] or 0) >= floor]
        n = len(cand)
        if n < MIN_NAMES:
            continue
        cand.sort(key=lambda t: t[0])
        k = max(2, int(n * DEFAULT_SIDE_FRACTION))
        shorts, longs = cand[:k], cand[-k:]
        mid = cand[n // 2][0]
        wl, ws = _weights(longs, k, mid, conviction), _weights(shorts, k, mid, conviction)
        long_ret = sum(w * r["excess_ret"] for w, (_, r) in zip(wl, longs))
        short_ret = sum(w * r["excess_ret"] for w, (_, r) in zip(ws, shorts))
        borrow = sum(w * borrow_rate(r["dollar_vol"]) * (r["hold_days"] or 91) / 365 for w, (_, r) in zip(ws, shorts))
        gross = long_ret - short_ret
        net = gross - 2 * ROUND_TRIP_COST - borrow
        cur_long = {r["ticker"] for _, r in longs}
        cur_short = {r["ticker"] for _, r in shorts}
        turnover = None
        if prev_long or prev_short:
            turnover = 0.5 * ((1 - len(cur_long & prev_long) / k) + (1 - len(cur_short & prev_short) / k))
        prev_long, prev_short = cur_long, cur_short
        uni = sum(r["fwd_ret"] for r in rs if r["fwd_ret"] is not None) / len(rs)
        out.append((name, qtr, n, k, long_ret, short_ret, gross, 2 * ROUND_TRIP_COST, borrow, net, turnover, uni,
                    ",".join(r["ticker"] for _, r in reversed(longs)), ",".join(r["ticker"] for _, r in shorts)))
    return out


VARIANTS: list[tuple[str, str, Scorer, float, bool]] = [
    # (name, family, scorer, floor, conviction)
    ("default_4", "baseline", scorer_default, FLOORS[0], False),
    ("floor_1m", "floor", scorer_default, FLOORS[1], False),
    ("floor_2m", "floor", scorer_default, FLOORS[2], False),
    ("floor_5m", "floor", scorer_default, FLOORS[3], False),
    ("residual_pnav", "factor", scorer_residual(scorer_default), FLOORS[0], False),
    ("conviction", "weighting", scorer_default, FLOORS[0], True),
    ("plus_generosity", "input", scorer_plus("generosity"), FLOORS[0], False),
    ("plus_risk_wavg", "input", scorer_plus("wavg_risk_wf"), FLOORS[0], False),
    ("plus_risk_hi", "input", scorer_plus("pct_risk_hi_wf"), FLOORS[0], False),
    ("combined_1m", "combined", scorer_plus("generosity"), FLOORS[1], True),
]


def build_strategy_lab(con: duckdb.DuckDBPyConnection, log=print) -> str:
    build_risk_wf(con, log)
    build_lab_universe(con)
    df = con.execute("SELECT * FROM signals.lab_universe ORDER BY qtr, ticker").pl()
    rows = df.to_dicts()
    by_q: dict = {}
    for r in rows:
        by_q.setdefault(r["qtr"], []).append(r)

    period_rows: list[tuple] = []
    for name, _fam, scorer, floor, conviction in VARIANTS:
        period_rows += run_variant(name, by_q, scorer, floor, conviction)
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.lab_variant_periods (
            variant VARCHAR, qtr DATE, n_names INTEGER, n_side INTEGER, long_excess DOUBLE, short_excess DOUBLE,
            spread_gross DOUBLE, trading_cost DOUBLE, borrow_cost DOUBLE, spread_net DOUBLE, turnover DOUBLE,
            universe_ret DOUBLE, longs VARCHAR, shorts VARCHAR
        )
        """
    )
    con.executemany("INSERT INTO signals.lab_variant_periods VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", period_rows)

    fam = {v[0]: v[1] for v in VARIANTS}
    floor_of = {v[0]: v[2 + 1] for v in VARIANTS}
    summary: list[tuple] = []
    for name, family in fam.items():
        per = [r for r in period_rows if r[0] == name]
        if not per:
            continue
        gross = [r[6] for r in per]
        net = [r[9] for r in per]
        to = [r[10] for r in per if r[10] is not None]
        summary.append((name, family, floor_of[name], len(per), sum(1 for g in gross if g > 0),
                        sum(gross) / len(gross), _tstat(gross), min(gross),
                        sum(net) / len(net), _tstat(net), min(net), sum(1 for x in net if x > 0),
                        sum(r[8] for r in per) / len(per), sum(to) / len(to) if to else None,
                        sum(r[2] for r in per) / len(per), sum(r[3] for r in per) / len(per)))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.lab_variant_summary (
            variant VARCHAR, family VARCHAR, floor DOUBLE, n_quarters INTEGER, quarters_won INTEGER,
            mean_gross DOUBLE, gross_tstat DOUBLE, worst_gross DOUBLE, mean_net DOUBLE, net_tstat DOUBLE,
            worst_net DOUBLE, quarters_won_net INTEGER, mean_borrow DOUBLE, mean_turnover DOUBLE,
            mean_names DOUBLE, mean_side DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.lab_variant_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", summary)

    # single-signal test of the extra inputs, same method as the event backtest
    sig_rows: list[tuple] = []
    for qtr, rs in sorted(by_q.items()):
        if len(rs) < MIN_NAMES:
            continue
        ret = [r["excess_ret"] for r in rs]
        for s in EXTRA_SIGNALS:
            sig = [r.get(f"{s}_pct") for r in rs]
            ic = _spearman(sig, ret)
            spread, lo, hi = _quintile_spread(sig, ret)
            if ic is None:
                continue
            sig_rows.append((qtr, s, sum(1 for v in sig if v is not None), ic, spread, lo, hi))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.lab_signal_periods (
            qtr DATE, signal VARCHAR, n INTEGER, ic DOUBLE, q5_minus_q1 DOUBLE, q1_ret DOUBLE, q5_ret DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.lab_signal_periods VALUES (?,?,?,?,?,?,?)", sig_rows)
    sig_summary: list[tuple] = []
    for s, worse in EXTRA_SIGNALS.items():
        sgn = -1.0 if worse else 1.0
        per = [r for r in sig_rows if r[1] == s]
        if not per:
            continue
        ics = [sgn * r[3] for r in per]
        sp = [sgn * r[4] for r in per if r[4] is not None]
        sig_summary.append((s, "higher is worse" if worse else "higher is better", len(ics), sum(ics) / len(ics),
                            _tstat(ics), sum(1 for x in ics if x > 0) / len(ics),
                            sum(sp) / len(sp) if sp else None, _tstat(sp) if sp else None))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.lab_signal_summary (
            signal VARCHAR, expected_direction VARCHAR, n_periods INTEGER, mean_ic DOUBLE, ic_tstat DOUBLE,
            ic_hit_rate DOUBLE, mean_spread_dir DOUBLE, spread_tstat DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.lab_signal_summary VALUES (?,?,?,?,?,?,?,?)", sig_summary)

    _live_books(con)
    for r in summary:
        log(f"  {r[0]:16s} {r[4]}/{r[3]} gross {r[5]:+.3%} (t {r[6]:.1f}, worst {r[7]:+.2%})  net {r[8]:+.3%} "
            f"(worst {r[10]:+.2%}, {r[11]} up)  borrow {r[12]:.2%}  turnover {r[13] if r[13] is None else round(r[13], 2)}")
    return f"strategy lab: {len(summary)} variants over {len(by_q)} quarters; {len(sig_summary)} extra signals"


def _live_books(con: duckdb.DuckDBPyConnection) -> None:
    """Today's book at the $5m floor, and today's ranking on the five-input score, both from the
    existing live ranking plus current liquidity and generosity."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.lab_book_largecap AS
        WITH px AS (
            SELECT ticker, median(close * volume) AS dollar_vol
            FROM market.prices WHERE date > current_date - INTERVAL 180 DAY GROUP BY 1
        ),
        big AS (
            SELECT s.*, px.dollar_vol, rank() OVER (ORDER BY s.health DESC) AS rank_lc, count(*) OVER () AS n_lc
            FROM signals.strategy_latest s JOIN px USING (ticker)
            WHERE px.dollar_vol >= {FLOORS[3]}
        )
        SELECT *, CASE WHEN rank_lc <= greatest(2, floor(n_lc * {DEFAULT_SIDE_FRACTION})) THEN 'long'
                       WHEN rank_lc > n_lc - greatest(2, floor(n_lc * {DEFAULT_SIDE_FRACTION})) THEN 'short' END AS side_lc
        FROM big ORDER BY rank_lc
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.lab_book_5 AS
        WITH g AS (
            SELECT s.cik, CASE WHEN s.n_shared >= 5 THEN s.generosity END AS generosity
            FROM signals.stale_bdc s
            JOIN signals.strategy_latest l ON l.cik = s.cik AND l.period_end = s.period_end
        ),
        base AS (
            SELECT l.*, g.generosity,
                   CASE WHEN g.generosity IS NULL THEN NULL ELSE
                       (rank() OVER (PARTITION BY g.generosity IS NULL ORDER BY g.generosity) - 1
                        + 0.5 * (count(*) OVER (PARTITION BY g.generosity) - 1))
                       / nullif(count(g.generosity) OVER () - 1, 0) END AS generosity_rank
            FROM signals.strategy_latest l LEFT JOIN g USING (cik)
        ),
        scored AS (
            SELECT *, list_aggregate(list_filter([1 - pct_debt_below_90_rank, 1 - pct_debt_below_95_rank, debt_mark_rank,
                                                  nav_chg_4q_rank, 1 - generosity_rank], x -> x IS NOT NULL), 'avg') AS health5
            FROM base
        ),
        ranked AS (SELECT *, rank() OVER (ORDER BY health5 DESC) AS rank5, count(*) OVER () AS n5 FROM scored)
        SELECT *, CASE WHEN rank5 <= greatest(2, floor(n5 * {DEFAULT_SIDE_FRACTION})) THEN 'long'
                       WHEN rank5 > n5 - greatest(2, floor(n5 * {DEFAULT_SIDE_FRACTION})) THEN 'short' END AS side5
        FROM ranked ORDER BY rank5
        """
    )
