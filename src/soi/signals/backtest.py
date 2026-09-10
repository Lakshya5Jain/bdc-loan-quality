"""Point-in-time, walk-forward backtest of the BDC-level signals on public BDC stock returns.

Each quarter end Q gets one rebalance date, Q + REBALANCE_LAG_DAYS, by which time nearly every
10-Q and 10-K for Q has been filed. A BDC is in the cross-section for Q only if its filing for
Q was filed on or before the rebalance date and the quarter passed the reconciliation gate.
Positions are taken at the last close on or before the rebalance date and held to the next
rebalance date; returns include dividends (adjusted close). Every return is measured in excess
of the equal-weight universe return, so the numbers describe long/short quality spread, not
BDC beta.

For every signal and every rebalance date:
  IC        rank correlation between the signal and the forward excess return
  Q5 - Q1   mean forward excess return of the highest-signal quintile less the lowest
For credit signals "higher = worse book", so a working signal shows a negative IC and a
negative Q5 - Q1 (the short leg loses, the long leg wins).

The composite is fitted walk-forward: at each rebalance date its component weights are the
mean IC of each component over the rebalance dates already observed (at least MIN_FIT_PERIODS),
sign-flipped so that a positive composite means "expected to outperform". No period's own
outcome ever enters its own weights.

Known limits: the universe is today's public tickers, so BDCs acquired or delisted since 2022
are missing (survivorship); the loan risk score's weights are fitted on the whole sample, so
wavg_risk is reported but kept out of the composite; 15 quarters is one credit cycle.

Tables: signals.bt_universe (one row per BDC per rebalance date with every signal and the
forward return), signals.bt_periods (per signal per date), signals.bt_summary (per signal).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import duckdb
import polars as pl

REBALANCE_LAG_DAYS = 75
MIN_FIT_PERIODS = 4
MIN_NAMES = 12
ROUND_TRIP_COST = 0.004  # 40 bp round trip on each leg, applied to the quintile spread
# a name is tradable at a date only if its median daily dollar volume over the prior 120
# trading days is at least this (Firsthand at 3 cents and Franklin BSP at zero volume are not)
MIN_DOLLAR_VOLUME = 100_000


@dataclass(frozen=True)
class Signal:
    name: str
    sql: str
    higher_is_worse: bool
    family: str


# (column expression over the joined universe row, higher = worse book?, family)
SIGNALS: list[Signal] = [
    # level of stress in the marks
    Signal("pct_debt_below_90", "b.pct_debt_below_90", True, "marks"),
    Signal("pct_debt_below_95", "b.pct_debt_below_95", True, "marks"),
    Signal("debt_mark", "b.debt_mark", False, "marks"),
    Signal("wavg_risk", "v.wavg_risk", True, "marks"),
    Signal("nonaccrual_pct_cost", "b.nonaccrual_pct_cost", True, "marks"),
    Signal("pik_share", "b.pik_share", True, "marks"),
    # flow of new stress
    Signal("new_deterioration_rate", "b.new_deterioration_rate", True, "flow"),
    Signal("markdown_share", "b.markdown_share", True, "flow"),
    Signal("d1_debt_mark", "b.d1_debt_mark", False, "flow"),
    Signal("d4_pct_debt_below_90", "b.d4_pct_debt_below_90", True, "flow"),
    Signal("new_nonaccrual_rate", "b.new_nonaccrual_rate", True, "flow"),
    Signal("exit_loss_rate", "b.exit_loss_rate", True, "flow"),
    Signal("quality_score", "b.quality_score", True, "flow"),
    Signal("quality_trend_4q", "b.quality_trend_4q", True, "flow"),
    # cross-lender
    Signal("generosity", "g.generosity", True, "peers"),
    Signal("peer_nonaccrual_not_flagged", "g.n_peer_nonaccrual_not_flagged * 1.0 / nullif(g.n_shared, 0)", True, "peers"),
    # income and balance sheet
    Signal("div_coverage", "f.div_coverage", False, "income"),
    Signal("nii_roe", "f.nii_roe", False, "income"),
    Signal("leverage", "f.leverage", True, "income"),
    Signal("nav_chg_4q", "f.nav_chg_4q", False, "income"),
    Signal("nav_chg_1q", "f.nav_chg_1q", False, "income"),
    Signal("realized_gl_rate", "f.realized_gl_rate", False, "income"),
    Signal("nii_chg_4q", "f.nii_chg_4q", False, "income"),
    # valuation and price
    Signal("p_nav", "u.p_nav", True, "price"),
    Signal("ret_6m", "u.ret_6m", False, "price"),
    Signal("ret_3m", "u.ret_3m", False, "price"),
]

COMPOSITE_FAMILIES = ("marks", "flow", "peers", "income")
# signals whose inputs were fitted on the whole sample (the loan risk model's weights) or that
# are themselves fitted: kept in the tables for reference, never inside the composite
EXCLUDE_FROM_COMPOSITE = {"wavg_risk", "quality_score", "quality_trend_4q"}


def _build_universe(con: duckdb.DuckDBPyConnection) -> None:
    sig_cols = ",\n".join(f"{s.sql} AS {s.name}" for s in SIGNALS if not s.sql.startswith("u."))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.bt_universe AS
        WITH quarters AS (
            SELECT DISTINCT period_end FROM signals.bdc_quarter
            WHERE period_end >= DATE '2022-09-30'
              AND extract(month FROM period_end) IN (3, 6, 9, 12)
        ),
        dates AS (
            SELECT period_end, period_end + INTERVAL {REBALANCE_LAG_DAYS} DAY AS rebal_date,
                   lead(period_end + INTERVAL {REBALANCE_LAG_DAYS} DAY) OVER (ORDER BY period_end) AS next_rebal
            FROM quarters
        ),
        px AS (SELECT ticker, date, close, adj_close FROM market.prices),
        -- last close on or before a date
        at_date AS (
            SELECT d.period_end, d.rebal_date, d.next_rebal, m.cik, m.ticker,
                   (SELECT adj_close FROM px WHERE px.ticker = m.ticker AND px.date <= d.rebal_date
                       AND px.date > d.rebal_date - INTERVAL 10 DAY ORDER BY px.date DESC LIMIT 1) AS adj_entry,
                   (SELECT close FROM px WHERE px.ticker = m.ticker AND px.date <= d.rebal_date
                       AND px.date > d.rebal_date - INTERVAL 10 DAY ORDER BY px.date DESC LIMIT 1) AS close_entry,
                   (SELECT adj_close FROM px WHERE px.ticker = m.ticker AND px.date <= d.next_rebal
                       AND px.date > d.next_rebal - INTERVAL 10 DAY ORDER BY px.date DESC LIMIT 1) AS adj_exit,
                   (SELECT adj_close FROM px WHERE px.ticker = m.ticker AND px.date <= d.rebal_date - INTERVAL 182 DAY
                       AND px.date > d.rebal_date - INTERVAL 196 DAY ORDER BY px.date DESC LIMIT 1) AS adj_6m_ago,
                   (SELECT adj_close FROM px WHERE px.ticker = m.ticker AND px.date <= d.rebal_date - INTERVAL 91 DAY
                       AND px.date > d.rebal_date - INTERVAL 105 DAY ORDER BY px.date DESC LIMIT 1) AS adj_3m_ago
            FROM dates d CROSS JOIN ref.bdc_master m
            WHERE m.is_public AND m.ticker IS NOT NULL
        ),
        avail AS (
            SELECT cik, period AS period_end, min(filed) AS first_filed FROM core.filings
            WHERE form IN ('10-Q', '10-K', '10-KT', '10-QT') GROUP BY 1, 2
        ),
        liq AS (
            SELECT d.rebal_date, p.ticker, median(p.close * p.volume) AS dollar_vol
            FROM dates d JOIN market.prices p ON p.date <= d.rebal_date AND p.date > d.rebal_date - INTERVAL 180 DAY
            GROUP BY 1, 2
        ),
        u AS (
            SELECT a.*, coalesce(av.first_filed, ps.filed) AS filed, ps.adsh,
                   a.adj_exit / nullif(a.adj_entry, 0) - 1 AS fwd_ret,
                   a.adj_entry / nullif(a.adj_6m_ago, 0) - 1 AS ret_6m,
                   a.adj_entry / nullif(a.adj_3m_ago, 0) - 1 AS ret_3m,
                   a.close_entry / nullif(f.nav_per_share, 0) AS p_nav
            FROM at_date a
            JOIN core.period_source ps ON ps.cik = a.cik AND ps.period_end = a.period_end
            LEFT JOIN avail av ON av.cik = a.cik AND av.period_end = a.period_end
            LEFT JOIN signals.bdc_fundamentals f ON f.cik = a.cik AND f.period_end = a.period_end
            JOIN liq ON liq.rebal_date = a.rebal_date AND liq.ticker = a.ticker
            WHERE coalesce(av.first_filed, ps.filed) <= a.rebal_date
              AND liq.dollar_vol >= {MIN_DOLLAR_VOLUME}
        )
        SELECT u.period_end, u.rebal_date, u.next_rebal, u.cik, u.ticker, u.filed,
               u.fwd_ret, u.ret_6m, u.ret_3m, u.p_nav, b.data_ok, b.n_debt,
               {sig_cols}
        FROM u
        JOIN signals.bdc_quarter b ON b.cik = u.cik AND b.period_end = u.period_end
        LEFT JOIN signals.bdc_generosity g ON g.cik = u.cik AND g.period_end = u.period_end
        LEFT JOIN signals.bdc_validated v ON v.cik = u.cik AND v.period_end = u.period_end
        LEFT JOIN signals.bdc_fundamentals f ON f.cik = u.cik AND f.period_end = u.period_end
        WHERE b.data_ok AND b.n_debt >= 10 AND u.adj_entry IS NOT NULL AND u.close_entry IS NOT NULL
        """
    )
    # excess return over the equal-weight universe at each rebalance date
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bt_universe AS
        SELECT u.*, u.fwd_ret - avg(u.fwd_ret) OVER (PARTITION BY u.rebal_date) AS excess_ret,
               avg(u.fwd_ret) OVER (PARTITION BY u.rebal_date) AS universe_ret,
               count(*) OVER (PARTITION BY u.rebal_date) AS n_universe
        FROM signals.bt_universe u
        """
    )


def _rank(values: list[float | None]) -> list[float | None]:
    idx = [i for i, v in enumerate(values) if v is not None and not (isinstance(v, float) and math.isnan(v))]
    order = sorted(idx, key=lambda i: values[i])
    out: list[float | None] = [None] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def _spearman(x: list[float | None], y: list[float | None]) -> float | None:
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 6:
        return None
    rx = _rank([p[0] for p in pairs])
    ry = _rank([p[1] for p in pairs])
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n  # type: ignore[arg-type]
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))  # type: ignore[operator]
    sxx = sum((a - mx) ** 2 for a in rx)  # type: ignore[operator]
    syy = sum((b - my) ** 2 for b in ry)  # type: ignore[operator]
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def _quintile_spread(sig: list[float | None], ret: list[float | None]) -> tuple[float | None, float | None, float | None]:
    pairs = sorted((s, r) for s, r in zip(sig, ret) if s is not None and r is not None)
    n = len(pairs)
    if n < MIN_NAMES:
        return None, None, None
    k = max(2, n // 5)
    low = [r for _, r in pairs[:k]]
    high = [r for _, r in pairs[-k:]]
    lo, hi = sum(low) / len(low), sum(high) / len(high)
    return hi - lo, lo, hi


def _zscores(values: list[float | None]) -> list[float | None]:
    xs = [v for v in values if v is not None]
    if len(xs) < 3:
        return [None] * len(values)
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((v - m) ** 2 for v in xs) / (len(xs) - 1))
    if sd == 0:
        return [None] * len(values)
    return [None if v is None else max(-3.0, min(3.0, (v - m) / sd)) for v in values]


def _tstat(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd > 0 else None


def run_backtest(con: duckdb.DuckDBPyConnection, log=print) -> str:
    _build_universe(con)
    df = con.execute("SELECT * FROM signals.bt_universe ORDER BY rebal_date, ticker").pl()
    dates = df["rebal_date"].unique(maintain_order=True).to_list()
    names = [s.name for s in SIGNALS]
    sign = {s.name: (-1.0 if s.higher_is_worse else 1.0) for s in SIGNALS}
    family = {s.name: s.family for s in SIGNALS}

    period_rows: list[tuple] = []
    ic_history: dict[str, list[float]] = {n: [] for n in names}
    composite_rows: list[tuple] = []

    for d in dates:
        g = df.filter(pl.col("rebal_date") == d)
        if g.height < MIN_NAMES:
            continue
        ret = g["excess_ret"].to_list()
        # walk-forward composite weights from the ICs of the periods already seen
        weights: dict[str, float] = {}
        for n in names:
            if family[n] not in COMPOSITE_FAMILIES or n in EXCLUDE_FROM_COMPOSITE:
                continue
            hist = ic_history[n]
            if len(hist) >= MIN_FIT_PERIODS:
                w = sum(hist) / len(hist)
                if abs(w) >= 0.03:
                    weights[n] = w  # sign carries direction: positive IC means higher signal, higher return
        comp: list[float | None] | None = None
        if weights:
            zs = {n: _zscores(g[n].to_list()) for n in weights}
            comp = []
            for i in range(g.height):
                num = 0.0
                den = 0.0
                for n, w in weights.items():
                    z = zs[n][i]
                    if z is not None:
                        num += w * z
                        den += abs(w)
                comp.append(num / den if den > 0 else None)
        for n in names:
            sig = g[n].to_list()
            ic = _spearman(sig, ret)
            spread, lo, hi = _quintile_spread(sig, ret)
            period_rows.append((d, g["period_end"][0], n, family[n], sum(1 for s in sig if s is not None),
                                ic, spread, lo, hi, g["universe_ret"][0]))
            if ic is not None:
                ic_history[n].append(ic)
        if comp is not None:
            ic = _spearman(comp, ret)
            spread, lo, hi = _quintile_spread(comp, ret)
            composite_rows.append((d, g["period_end"][0], "composite_walk_forward", "composite",
                                   sum(1 for c in comp if c is not None), ic, spread, lo, hi,
                                   g["universe_ret"][0]))
            # record which components carried weight that period
            log(f"  {d}: composite over {len(weights)} components: "
                + ", ".join(f"{n}{'+' if w > 0 else '-'}" for n, w in sorted(weights.items(), key=lambda t: -abs(t[1]))[:6]))

    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bt_periods (
            rebal_date DATE, period_end DATE, signal VARCHAR, family VARCHAR, n INTEGER,
            ic DOUBLE, q5_minus_q1 DOUBLE, q1_ret DOUBLE, q5_ret DOUBLE, universe_ret DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.bt_periods VALUES (?,?,?,?,?,?,?,?,?,?)", period_rows + composite_rows)

    # summary per signal: mean IC, its t-stat, hit rate in the signal's expected direction,
    # mean quintile spread in the expected direction, annualised, after a round-trip cost
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bt_summary (
            signal VARCHAR, family VARCHAR, expected_direction VARCHAR, n_periods INTEGER,
            mean_ic DOUBLE, ic_tstat DOUBLE, ic_hit_rate DOUBLE,
            mean_spread_dir DOUBLE, spread_tstat DOUBLE, ann_spread_net DOUBLE, first_date DATE, last_date DATE
        )
        """
    )
    rows = []
    all_names = names + ["composite_walk_forward"]
    for n in all_names:
        s = 1.0 if n == "composite_walk_forward" else sign[n]
        fam = "composite" if n == "composite_walk_forward" else family[n]
        per = [r for r in period_rows + composite_rows if r[2] == n and r[5] is not None]
        if not per:
            continue
        ics = [s * r[5] for r in per]
        spreads = [s * r[6] for r in per if r[6] is not None]
        hit = sum(1 for x in ics if x > 0) / len(ics)
        mean_spread = sum(spreads) / len(spreads) if spreads else None
        ann = (4 * (mean_spread - ROUND_TRIP_COST)) if mean_spread is not None else None
        rows.append((n, fam, "higher is better" if s > 0 else "higher is worse", len(ics),
                     sum(ics) / len(ics), _tstat(ics), hit, mean_spread, _tstat(spreads) if spreads else None,
                     ann, per[0][0], per[-1][0]))
    con.executemany("INSERT INTO signals.bt_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    # the live cross-section: composite at the most recent rebalance date, with the weights
    # fitted on every completed period, plus each name's rank on the strongest single signals
    last = dates[-1]
    g = df.filter(pl.col("rebal_date") == last)
    weights = {}
    for n in names:
        if family[n] in COMPOSITE_FAMILIES and n not in EXCLUDE_FROM_COMPOSITE:
            hist = ic_history[n]
            if len(hist) >= MIN_FIT_PERIODS and abs(sum(hist) / len(hist)) >= 0.03:
                weights[n] = sum(hist) / len(hist)
    zs = {n: _zscores(g[n].to_list()) for n in weights}
    live = []
    for i in range(g.height):
        num = den = 0.0
        contrib = []
        for n, w in weights.items():
            z = zs[n][i]
            if z is not None:
                num += w * z
                den += abs(w)
                contrib.append((w * z, n))
        score = num / den if den > 0 else None
        contrib.sort()
        live.append((g["ticker"][i], g["period_end"][i], last, score,
                     ", ".join(n for _, n in contrib[:3]), ", ".join(n for _, n in contrib[-3:][::-1]),
                     g["pct_debt_below_90"][i], g["debt_mark"][i], g["nav_chg_4q"][i], g["p_nav"][i]))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bt_latest AS
        SELECT * FROM (VALUES (NULL::VARCHAR, NULL::DATE, NULL::DATE, NULL::DOUBLE, NULL::VARCHAR, NULL::VARCHAR,
                               NULL::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, NULL::DOUBLE))
             t(ticker, period_end, rebal_date, composite, drags, supports, pct_debt_below_90, debt_mark, nav_chg_4q, p_nav)
        WHERE FALSE
        """
    )
    con.executemany("INSERT INTO signals.bt_latest VALUES (?,?,?,?,?,?,?,?,?,?)", live)
    con.execute(
        "CREATE OR REPLACE TABLE signals.bt_weights AS SELECT * FROM (VALUES (NULL::VARCHAR, NULL::DOUBLE)) t(signal, weight) WHERE FALSE"
    )
    con.executemany("INSERT INTO signals.bt_weights VALUES (?, ?)", list(weights.items()))
    n_dates = len({r[0] for r in period_rows})
    n_names = df.select(pl.col("ticker").n_unique()).item()
    return f"backtest: {n_dates} rebalance dates, {n_names} BDCs, {len(names)} signals + walk-forward composite"


# ---- event-driven variant: trade the day after each filing --------------------------------------

EVENT_SIGNALS = ["pct_debt_below_90", "pct_debt_below_95", "debt_mark", "nav_chg_4q", "nav_chg_1q",
                 "new_deterioration_rate", "d4_pct_debt_below_90", "nonaccrual_pct_cost", "p_nav"]


def run_event_backtest(con: duckdb.DuckDBPyConnection, log=print) -> str:
    """Enter each BDC at the first close after its filing, exit at the first close after its
    next filing. The cross-section a name is ranked against is every other public BDC's most
    recent filing as of that entry date. Returns are in excess of the equal-weight return of
    the other names over the same window. Results are grouped by the calendar quarter of the
    period reported, so they line up with the quarterly-rebalance test.
    Tables: signals.bt_event_universe, signals.bt_event_periods, signals.bt_event_summary."""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.bt_event_universe AS
        WITH avail AS (
            SELECT cik, period AS period_end, min(filed) AS first_filed FROM core.filings
            WHERE form IN ('10-Q', '10-K', '10-KT', '10-QT') GROUP BY 1, 2
        ),
        ev AS (
            SELECT ps.cik, m.ticker, ps.period_end, coalesce(av.first_filed, ps.filed) AS filed,
                   lead(coalesce(av.first_filed, ps.filed)) OVER (PARTITION BY ps.cik ORDER BY ps.period_end) AS next_filed
            FROM core.period_source ps JOIN ref.bdc_master m USING (cik)
            LEFT JOIN avail av ON av.cik = ps.cik AND av.period_end = ps.period_end
            JOIN signals.bdc_quarter b ON b.cik = ps.cik AND b.period_end = ps.period_end
            WHERE m.is_public AND m.ticker IS NOT NULL AND b.data_ok AND b.n_debt >= 10
              AND ps.period_end >= DATE '2022-09-30'
              AND extract(month FROM ps.period_end) IN (3, 6, 9, 12)
        ),
        px AS (SELECT ticker, date, close, adj_close FROM market.prices),
        px2 AS (SELECT ticker, date, close, volume FROM market.prices),
        dated AS (
            SELECT e.*,
                   (SELECT min(date) FROM px WHERE px.ticker = e.ticker AND px.date > e.filed) AS entry_date,
                   (SELECT min(date) FROM px WHERE px.ticker = e.ticker AND px.date > e.next_filed) AS exit_date
            FROM ev e
        ),
        priced AS (
            SELECT d.*,
                   (SELECT median(close * volume) FROM px2 WHERE px2.ticker = d.ticker
                       AND px2.date <= d.entry_date AND px2.date > d.entry_date - INTERVAL 180 DAY) AS dollar_vol,
                   (SELECT adj_close FROM px WHERE px.ticker = d.ticker AND px.date = d.entry_date) AS adj_entry,
                   (SELECT close FROM px WHERE px.ticker = d.ticker AND px.date = d.entry_date) AS close_entry,
                   (SELECT adj_close FROM px WHERE px.ticker = d.ticker AND px.date = d.exit_date) AS adj_exit
            FROM dated d WHERE d.entry_date IS NOT NULL AND d.exit_date IS NOT NULL
        ),
        -- equal-weight return of every other public name over the same window
        bench AS (
            SELECT p.cik, p.period_end,
                   avg(o.adj_exit / o.adj_entry - 1) AS bench_ret
            FROM priced p
            JOIN (
                SELECT m.ticker,
                       p2.entry_date, p2.exit_date,
                       (SELECT adj_close FROM px WHERE px.ticker = m.ticker AND px.date = p2.entry_date) AS adj_entry,
                       (SELECT adj_close FROM px WHERE px.ticker = m.ticker AND px.date = p2.exit_date) AS adj_exit
                FROM (SELECT DISTINCT entry_date, exit_date FROM priced) p2
                CROSS JOIN ref.bdc_master m WHERE m.is_public AND m.ticker IS NOT NULL
            ) o ON o.entry_date = p.entry_date AND o.exit_date = p.exit_date AND o.ticker <> p.ticker
                 AND o.adj_entry > 0 AND o.adj_exit > 0
            GROUP BY 1, 2
        ),
        sig AS (
            SELECT p.*, b.pct_debt_below_90, b.pct_debt_below_95, b.debt_mark, b.new_deterioration_rate,
                   b.d4_pct_debt_below_90, b.nonaccrual_pct_cost, f.nav_chg_4q, f.nav_chg_1q,
                   p.close_entry / nullif(f.nav_per_share, 0) AS p_nav,
                   p.adj_exit / p.adj_entry - 1 AS fwd_ret, bn.bench_ret,
                   p.adj_exit / p.adj_entry - 1 - bn.bench_ret AS excess_ret,
                   date_diff('day', p.entry_date, p.exit_date) AS hold_days
            FROM priced p
            JOIN signals.bdc_quarter b ON b.cik = p.cik AND b.period_end = p.period_end
            LEFT JOIN signals.bdc_fundamentals f ON f.cik = p.cik AND f.period_end = p.period_end
            JOIN bench bn ON bn.cik = p.cik AND bn.period_end = p.period_end
        )
        SELECT * FROM sig WHERE adj_entry > 0 AND adj_exit > 0 AND hold_days BETWEEN 20 AND 200
          AND dollar_vol >= {MIN_DOLLAR_VOLUME}
        """
    )
    # percentile of each signal against every other name's latest filing as of the entry date
    for s in EVENT_SIGNALS:
        con.execute(
            f"""
            ALTER TABLE signals.bt_event_universe ADD COLUMN IF NOT EXISTS {s}_pct DOUBLE;
            UPDATE signals.bt_event_universe u SET {s}_pct = (
                SELECT avg((o.{s} < u.{s})::INT) + 0.5 * avg((o.{s} = u.{s})::INT)
                FROM (
                    SELECT v.cik, arg_max(v.{s}, v.period_end) AS {s}
                    FROM signals.bt_event_universe v
                    WHERE v.cik <> u.cik AND v.filed <= u.entry_date AND v.{s} IS NOT NULL
                      AND v.period_end >= u.period_end - INTERVAL 200 DAY
                    GROUP BY v.cik
                ) o
            ) WHERE u.{s} IS NOT NULL
            """
        )
    df = con.execute("SELECT * FROM signals.bt_event_universe ORDER BY period_end, ticker").pl()
    higher_worse = {s.name: s.higher_is_worse for s in SIGNALS}
    rows: list[tuple] = []
    for pe in df["period_end"].unique(maintain_order=True).to_list():
        g = df.filter(pl.col("period_end") == pe)
        if g.height < MIN_NAMES:
            continue
        ret = g["excess_ret"].to_list()
        for s in EVENT_SIGNALS:
            sig = g[f"{s}_pct"].to_list()
            ic = _spearman(sig, ret)
            spread, lo, hi = _quintile_spread(sig, ret)
            rows.append((pe, s, g.height, ic, spread, lo, hi, float(g["hold_days"].mean())))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bt_event_periods (
            period_end DATE, signal VARCHAR, n INTEGER, ic DOUBLE, q5_minus_q1 DOUBLE,
            q1_ret DOUBLE, q5_ret DOUBLE, avg_hold_days DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.bt_event_periods VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.bt_event_summary (
            signal VARCHAR, n_periods INTEGER, mean_ic DOUBLE, ic_tstat DOUBLE, ic_hit_rate DOUBLE,
            mean_spread_dir DOUBLE, spread_tstat DOUBLE, ann_spread_net DOUBLE, avg_hold_days DOUBLE
        )
        """
    )
    out = []
    for s in EVENT_SIGNALS:
        sgn = -1.0 if higher_worse[s] else 1.0
        per = [r for r in rows if r[1] == s and r[3] is not None]
        if not per:
            continue
        ics = [sgn * r[3] for r in per]
        sp = [sgn * r[4] for r in per if r[4] is not None]
        hold = sum(r[7] for r in per) / len(per)
        mean_sp = sum(sp) / len(sp) if sp else None
        ann = ((365 / hold) * (mean_sp - ROUND_TRIP_COST)) if mean_sp is not None and hold > 0 else None
        out.append((s, len(ics), sum(ics) / len(ics), _tstat(ics), sum(1 for x in ics if x > 0) / len(ics),
                    mean_sp, _tstat(sp) if sp else None, ann, hold))
    con.executemany("INSERT INTO signals.bt_event_summary VALUES (?,?,?,?,?,?,?,?,?)", out)
    n = con.execute("SELECT count(*), count(DISTINCT period_end) FROM signals.bt_event_universe").fetchone()
    log(build_default_strategy(con))
    return f"event backtest: {n[0]} filings, {n[1]} quarters, {len(EVENT_SIGNALS)} signals"


# ---- the default strategy ---------------------------------------------------------------------

DEFAULT_SIGNALS = ("pct_debt_below_90", "pct_debt_below_95", "debt_mark", "nav_chg_4q")
DEFAULT_SIDE_FRACTION = 0.2


def _health(row: dict) -> float | None:
    """Average of the four peer-percentiles, oriented so that higher = healthier."""
    vals = []
    for s in DEFAULT_SIGNALS:
        v = row.get(f"{s}_pct")
        if v is None:
            continue
        vals.append(v if s in ("debt_mark", "nav_chg_4q") else 1.0 - v)
    return sum(vals) / len(vals) if len(vals) >= 2 else None


def build_default_strategy(con: duckdb.DuckDBPyConnection) -> str:
    """Filing-day long/short on the health score: long the healthiest fifth, short the sickest
    fifth, equal weight, hold to the next filing. Writes:
      signals.strategy_periods  one row per reported quarter: long, short, spread, universe
      signals.strategy_latest   the live book: every liquid public BDC ranked, with side and the
                                four inputs, from its most recent filing
      signals.strategy_summary  one row of headline statistics"""
    df = con.execute("SELECT * FROM signals.bt_event_universe ORDER BY period_end, ticker").pl()
    rows = df.to_dicts()
    periods: list[tuple] = []
    by_q: dict = {}
    for r in rows:
        by_q.setdefault(r["period_end"], []).append(r)
    for pe, rs in sorted(by_q.items()):
        scored = sorted(((_health(r), r) for r in rs if _health(r) is not None and r["excess_ret"] is not None), key=lambda t: t[0])
        if len(scored) < MIN_NAMES:
            continue
        k = max(2, int(len(scored) * DEFAULT_SIDE_FRACTION))
        shorts, longs = scored[:k], scored[-k:]
        long_ret = sum(r["excess_ret"] for _, r in longs) / k
        short_ret = sum(r["excess_ret"] for _, r in shorts) / k
        uni = sum(r["fwd_ret"] for r in rs if r["fwd_ret"] is not None) / len(rs)
        periods.append((pe, len(scored), k, long_ret, short_ret, long_ret - short_ret, uni,
                        ",".join(r["ticker"] for _, r in reversed(longs)), ",".join(r["ticker"] for _, r in shorts)))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.strategy_periods (
            period_end DATE, n_names INTEGER, n_side INTEGER, long_excess DOUBLE, short_excess DOUBLE,
            spread DOUBLE, universe_ret DOUBLE, longs VARCHAR, shorts VARCHAR
        )
        """
    )
    con.executemany("INSERT INTO signals.strategy_periods VALUES (?,?,?,?,?,?,?,?,?)", periods)

    # live book: most recent filing per liquid public name, ranked against the others
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.strategy_latest AS
        WITH avail AS (
            SELECT cik, period AS period_end, min(filed) AS first_filed FROM core.filings
            WHERE form IN ('10-Q', '10-K', '10-KT', '10-QT') GROUP BY 1, 2
        ),
        lastq AS (
            SELECT b.cik, b.period_end, b.pct_debt_below_90, b.pct_debt_below_95, b.debt_mark, f.nav_chg_4q,
                   f.nav_per_share, coalesce(av.first_filed, ps.filed) AS filed,
                   row_number() OVER (PARTITION BY b.cik ORDER BY b.period_end DESC) AS rn
            FROM signals.bdc_quarter b
            JOIN ref.bdc_master m ON m.cik = b.cik
            JOIN core.period_source ps ON ps.cik = b.cik AND ps.period_end = b.period_end
            LEFT JOIN avail av ON av.cik = b.cik AND av.period_end = b.period_end
            LEFT JOIN signals.bdc_fundamentals f ON f.cik = b.cik AND f.period_end = b.period_end
            WHERE m.is_public AND m.ticker IS NOT NULL AND b.data_ok AND b.n_debt >= 10
        ),
        px AS (
            SELECT ticker, arg_max(close, date) AS close_entry, max(date) AS entry_date,
                   median(close * volume) AS dollar_vol
            FROM market.prices WHERE date > current_date - INTERVAL 180 DAY GROUP BY 1
        ),
        cur AS (
            SELECT l.*, m.ticker, px.close_entry, px.entry_date, px.close_entry / nullif(l.nav_per_share, 0) AS p_nav
            FROM lastq l JOIN ref.bdc_master m ON m.cik = l.cik JOIN px ON px.ticker = m.ticker
            WHERE l.rn = 1 AND l.period_end >= (SELECT max(period_end) FROM lastq) - INTERVAL 200 DAY
              AND px.dollar_vol >= {MIN_DOLLAR_VOLUME}
        ),
        pct AS (
            SELECT c.cik, c.ticker, c.period_end, c.filed, c.entry_date, c.close_entry, c.p_nav,
                   c.pct_debt_below_90, c.pct_debt_below_95, c.debt_mark, c.nav_chg_4q,
                   {", ".join(f"percent_rank() OVER (ORDER BY c.{s}) AS {s}_rank" for s in DEFAULT_SIGNALS)}
            FROM cur c
        ),
        scored AS (
            SELECT *,
                   ((1 - pct_debt_below_90_rank) + (1 - pct_debt_below_95_rank) + debt_mark_rank + nav_chg_4q_rank) / 4 AS health
            FROM pct
            WHERE pct_debt_below_90 IS NOT NULL AND pct_debt_below_95 IS NOT NULL AND debt_mark IS NOT NULL
        ),
        ranked AS (
            SELECT *, rank() OVER (ORDER BY health DESC) AS rank, count(*) OVER () AS n
            FROM scored
        )
        SELECT r.*, m.name,
               CASE WHEN rank <= greatest(2, floor(n * {DEFAULT_SIDE_FRACTION})) THEN 'long'
                    WHEN rank > n - greatest(2, floor(n * {DEFAULT_SIDE_FRACTION})) THEN 'short'
                    ELSE NULL END AS side
        FROM ranked r JOIN ref.bdc_master m USING (cik)
        ORDER BY rank
        """
    )
    won = sum(1 for p in periods if p[5] > 0)
    spreads = [p[5] for p in periods]
    mean = sum(spreads) / len(spreads) if spreads else None
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.strategy_summary AS
        SELECT ? AS n_quarters, ? AS quarters_won, ? AS mean_spread, ? AS worst_spread, ? AS best_spread,
               ? AS spread_tstat, ? AS first_period, ? AS last_period,
               (SELECT count(*) FROM signals.strategy_latest) AS n_names_now,
               (SELECT count(*) FROM signals.strategy_latest WHERE side = 'long') AS n_long_now,
               (SELECT max(period_end) FROM signals.strategy_latest) AS latest_period,
               (SELECT max(entry_date) FROM signals.strategy_latest) AS latest_entry_date
        """,
        [len(periods), won, mean, min(spreads) if spreads else None, max(spreads) if spreads else None,
         _tstat(spreads), periods[0][0] if periods else None, periods[-1][0] if periods else None],
    )
    return f"default strategy: {won} of {len(periods)} quarters positive, mean spread {mean:.3%}" if mean is not None else "default strategy: no periods"
