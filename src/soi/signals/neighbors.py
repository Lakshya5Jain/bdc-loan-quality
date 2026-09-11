"""Neighbours of distress: when a borrower goes bad, which near-par borrowers look like it?

Borrower-quarter feature vector (debt positions only, structured products excluded, quarters that passed the data check,
summed across every lender holding the borrower): first-lien share of cost, cost-weighted
rate, spread, PIK share, months to maturity, log10 of total cost, number of lenders, the
cost-weighted mark, and a broad sector where any lender tagged an industry (about 13% of
borrower-quarters; the tag is sparse in the filings).

Distress event: the first quarter a borrower's mark drops below 0.90 or any lender puts it on
non-accrual, having been observed above 0.90 and accruing in an earlier quarter.

Neighbours: for each event at quarter t, the 10 near-par borrowers (mark >= 0.97 at t) closest
to the event borrower's profile in the quarter before it went bad (the mark itself is left out
of the distance, since every candidate is near par). Distances are on features standardised
within the quarter; a pair needs at least three features in common. Candidates in the same
sector come first when the event borrower's sector is known.

Controls: for each event, 10 borrowers drawn at random from the same near-par pool with the
same lien bucket (mostly first lien or not).

Outcome: the neighbour's mark falls below 0.95 within four quarters (380 days) of t. A pair is
observed only if the neighbour is seen again at least 270 days after t or the fall happened.

Test: hit rate for neighbours against controls, with a bootstrap over events (clustered, since
one event contributes ten pairs) for the 95% interval on the difference.

Tables: signals.nbr_features, signals.nbr_events, signals.nbr_pairs, signals.nbr_test,
signals.nbr_watchlist (neighbours of events from the last two quarters, with today's holders).
"""
from __future__ import annotations

import random
import warnings

import duckdb
import numpy as np

K = 10
NEAR_PAR = 0.97
OUTCOME_MARK = 0.95
OUTCOME_DAYS = 380
OBSERVED_DAYS = 270
MIN_DIMS = 3
BOOTSTRAP = 1000
DIST_FEATURES = ["lien_first_share", "rate", "spread", "pik_share", "months_to_maturity", "log_cost", "n_lenders"]

SECTOR_SQL = """
    CASE
        WHEN ind IS NULL THEN NULL
        WHEN ind LIKE '%health%' OR ind LIKE '%pharma%' OR ind LIKE '%medical%' OR ind LIKE '%biotech%' OR ind LIKE '%life science%' THEN 'healthcare'
        WHEN ind LIKE '%software%' OR ind LIKE '%tech%' OR ind LIKE '%internet%' OR ind LIKE '%it service%' OR ind LIKE '%information%' OR ind LIKE '%data%' OR ind LIKE '%semiconductor%' OR ind LIKE '%electronic%' THEN 'technology'
        WHEN ind LIKE '%business service%' OR ind LIKE '%servicesbusiness%' OR ind LIKE '%servicebusiness%' OR ind LIKE '%professional%' OR ind LIKE '%commercial service%' OR ind LIKE '%commercialandindustrial%' OR ind LIKE '%diversified support%' OR ind LIKE '%human resource%' OR ind LIKE '%research%' THEN 'business services'
        WHEN ind LIKE '%financ%' OR ind LIKE '%insurance%' OR ind LIKE '%bank%' OR ind LIKE '%capital market%' OR ind LIKE '%asset management%' THEN 'financials'
        WHEN ind LIKE '%consumer%' OR ind LIKE '%retail%' OR ind LIKE '%restaurant%' OR ind LIKE '%food%' OR ind LIKE '%beverage%' OR ind LIKE '%leisure%' OR ind LIKE '%hotel%' OR ind LIKE '%apparel%' OR ind LIKE '%household%' OR ind LIKE '%personal%' OR ind LIKE '%education%' OR ind LIKE '%childcare%' OR ind LIKE '%gaming%' OR ind LIKE '%entertainment%' THEN 'consumer'
        WHEN ind LIKE '%media%' OR ind LIKE '%telecom%' OR ind LIKE '%broadcast%' OR ind LIKE '%publishing%' OR ind LIKE '%advertising%' OR ind LIKE '%communication%' THEN 'media & telecom'
        WHEN ind LIKE '%energy%' OR ind LIKE '%oil%' OR ind LIKE '%gas%' OR ind LIKE '%utilit%' OR ind LIKE '%power%' OR ind LIKE '%mining%' OR ind LIKE '%metal%' OR ind LIKE '%chemical%' THEN 'energy & materials'
        WHEN ind LIKE '%industrial%' OR ind LIKE '%capital equipment%' OR ind LIKE '%capitalequipment%' OR ind LIKE '%machinery%' OR ind LIKE '%aerospace%' OR ind LIKE '%defense%' OR ind LIKE '%construction%' OR ind LIKE '%building%' OR ind LIKE '%transport%' OR ind LIKE '%auto%' OR ind LIKE '%distribution%' OR ind LIKE '%wholesale%' OR ind LIKE '%packaging%' OR ind LIKE '%container%' OR ind LIKE '%environmental%' OR ind LIKE '%logistic%' THEN 'industrials'
        WHEN ind LIKE '%real estate%' OR ind LIKE '%reit%' THEN 'real estate'
        ELSE 'other'
    END
"""


def build_neighbors(con: duckdb.DuckDBPyConnection, log=print) -> str:
    _features(con)
    _events(con)
    n_pairs = _pairs(con, log)
    _test(con, log)
    _watchlist(con)
    n_ev = con.execute("SELECT count(*) FROM signals.nbr_events").fetchone()[0]
    return f"neighbours: {n_ev:,} distress events, {n_pairs:,} neighbour/control pairs"


def _features(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.nbr_features AS
        WITH q AS (
            SELECT issuer_norm AS borrower_key, period_end, cik, issuer_name, cost, fair_value, mark,
                   instrument_type, rate, spread, pik_rate, pik_flag, maturity, nonaccrual_flag,
                   lower(regexp_replace(regexp_replace(coalesce(industry, ''), '^.*#', ''), '(Sector)?Member$', '')) AS ind_raw
            FROM signals.loan_quarter
            WHERE is_debt AND data_ok AND cost > 0 AND fair_value IS NOT NULL
              AND issuer_norm <> '' AND length(issuer_norm) >= 4
              AND fair_value / cost BETWEEN 0 AND 1.25
              -- CLO tranches, JVs and funds are not loans to a company: their marks move for
              -- other reasons and they would dominate both the events and the neighbours
              AND instrument_type <> 'structured'
              -- CLO tranches tagged as plain notes ("MAGNE 2020-28", "CIFC 2025-3 CLO")
              -- matched on the normalised key so spacing and suffix variants are caught too
              AND NOT regexp_matches(issuer_norm, '^[a-z0-9]{{2,8}} [0-9]{{4}} [0-9]{{1,2}}[a-z]?( |$)')
              AND NOT regexp_matches(issuer_norm, '(^| )clo( |$)')
        ),
        ind AS (
            -- the industry most often tagged for the borrower by any lender in any quarter
            SELECT borrower_key, arg_max(ind_raw, c) AS ind
            FROM (SELECT borrower_key, ind_raw, count(*) AS c FROM q WHERE ind_raw <> '' GROUP BY 1, 2)
            GROUP BY 1
        ),
        agg AS (
            SELECT q.borrower_key, q.period_end,
                   any_value(issuer_name) AS issuer_name,
                   count(DISTINCT cik) AS n_lenders,
                   sum(cost) AS total_cost,
                   sum(fair_value) / sum(cost) AS mark,
                   bool_or(nonaccrual_flag) AS any_nonaccrual,
                   sum(cost) FILTER (WHERE instrument_type = 'first_lien') / sum(cost) AS lien_first_share,
                   sum(rate * cost) FILTER (WHERE rate BETWEEN 0.01 AND 0.35) / nullif(sum(cost) FILTER (WHERE rate BETWEEN 0.01 AND 0.35), 0) AS rate,
                   sum(spread * cost) FILTER (WHERE spread BETWEEN 0.005 AND 0.25) / nullif(sum(cost) FILTER (WHERE spread BETWEEN 0.005 AND 0.25), 0) AS spread,
                   sum(cost) FILTER (WHERE pik_flag OR pik_rate > 0) / sum(cost) AS pik_share,
                   sum(date_diff('day', period_end, maturity) / 30.4 * cost) FILTER (WHERE maturity > period_end AND maturity < period_end + INTERVAL 15 YEAR)
                       / nullif(sum(cost) FILTER (WHERE maturity > period_end AND maturity < period_end + INTERVAL 15 YEAR), 0) AS months_to_maturity,
                   any_value(i.ind) AS ind
            FROM q LEFT JOIN ind i USING (borrower_key) GROUP BY 1, 2
        )
        SELECT borrower_key, period_end, issuer_name, n_lenders, total_cost, log10(total_cost) AS log_cost, mark,
               any_nonaccrual, lien_first_share, rate, spread, pik_share, months_to_maturity,
               {SECTOR_SQL} AS sector
        FROM agg
        """
    )


def _events(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.nbr_events AS
        WITH f AS (
            SELECT *, (mark < 0.90 OR any_nonaccrual) AS distressed,
                   lag(period_end) OVER w AS prior_period, lag(mark) OVER w AS prior_mark,
                   lag(any_nonaccrual) OVER w AS prior_na
            FROM signals.nbr_features
            WINDOW w AS (PARTITION BY borrower_key ORDER BY period_end)
        ),
        first_bad AS (
            SELECT borrower_key, min(period_end) AS event_period FROM f WHERE distressed GROUP BY 1
        )
        SELECT f.borrower_key, f.period_end AS event_period, f.prior_period, f.issuer_name, f.sector,
               f.mark AS event_mark, f.any_nonaccrual AS event_nonaccrual, f.prior_mark
        FROM f JOIN first_bad b ON b.borrower_key = f.borrower_key AND b.event_period = f.period_end
        WHERE f.prior_period IS NOT NULL AND f.prior_mark >= 0.90 AND NOT coalesce(f.prior_na, FALSE)
          AND f.prior_period >= f.period_end - INTERVAL 200 DAY
        """
    )


def _pairs(con: duckdb.DuckDBPyConnection, log) -> int:
    feats = con.execute(
        f"""
        SELECT borrower_key, period_end, sector, mark, {", ".join(DIST_FEATURES)}
        FROM signals.nbr_features
        """
    ).fetchall()
    by_q: dict = {}
    for r in feats:
        by_q.setdefault(r[1], []).append(r)
    events = con.execute(
        "SELECT borrower_key, event_period, prior_period, sector FROM signals.nbr_events"
    ).fetchall()
    # the event borrower's profile in the quarter before it went bad
    prior = {(r[0], r[1]): r for r in feats}
    rng = random.Random(3)
    pairs: list[tuple] = []
    for qtr in sorted({e[1] for e in events}):
        cands = [r for r in by_q.get(qtr, []) if r[3] is not None and r[3] >= NEAR_PAR]
        if len(cands) < 50:
            continue
        keys = [r[0] for r in cands]
        sectors = [r[2] for r in cands]
        X = np.array([[np.nan if v is None else float(v) for v in r[4:]] for r in cands], dtype=float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # a feature nobody has this quarter
            mu = np.nanmean(X, axis=0)
            sd = np.nanstd(X, axis=0)
        mu[~np.isfinite(mu)] = 0.0
        sd[~np.isfinite(sd) | (sd == 0)] = 1.0
        Z = (X - mu) / sd
        lien_hi = X[:, 0] >= 0.5
        key_index = {k: i for i, k in enumerate(keys)}
        for ev_key, ev_q, prior_q, ev_sector in [e for e in events if e[1] == qtr]:
            p = prior.get((ev_key, prior_q))
            if p is None:
                continue
            e = np.array([np.nan if v is None else float(v) for v in p[4:]], dtype=float)
            ez = (e - mu) / sd
            diff = Z - ez
            ok = np.isfinite(diff)
            n_ok = ok.sum(axis=1)
            d = np.sqrt((np.where(ok, diff, 0.0) ** 2).sum(axis=1) / np.maximum(n_ok, 1))
            d[n_ok < MIN_DIMS] = np.inf
            if ev_key in key_index:
                d[key_index[ev_key]] = np.inf
            order = np.argsort(d)
            chosen: list[int] = []
            if ev_sector is not None:
                chosen = [int(i) for i in order if sectors[i] == ev_sector and np.isfinite(d[i])][:K]
            for i in order:
                if len(chosen) >= K:
                    break
                if np.isfinite(d[i]) and i not in chosen:
                    chosen.append(int(i))
            if len(chosen) < MIN_DIMS:
                continue
            for rank, i in enumerate(chosen, 1):
                pairs.append((ev_key, ev_q, keys[i], rank, float(d[i]), ev_sector is not None and sectors[i] == ev_sector, False))
            # matched random controls: same lien bucket, not already a neighbour, not the event
            ev_lien = e[0] >= 0.5 if np.isfinite(e[0]) else None
            pool = [i for i in range(len(cands)) if i not in chosen and keys[i] != ev_key
                    and (ev_lien is None or lien_hi[i] == ev_lien)]
            for rank, i in enumerate(rng.sample(pool, min(K, len(pool))), 1):
                pairs.append((ev_key, ev_q, keys[i], rank, float(d[i]) if np.isfinite(d[i]) else None, False, True))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.nbr_pairs_raw (
            borrower_key VARCHAR, event_period DATE, neighbor_key VARCHAR, rank INTEGER, distance DOUBLE,
            same_sector BOOLEAN, is_control BOOLEAN
        )
        """
    )
    con.executemany("INSERT INTO signals.nbr_pairs_raw VALUES (?,?,?,?,?,?,?)", pairs)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.nbr_pairs AS
        WITH latest AS (SELECT max(period_end) AS d FROM signals.nbr_features)
        SELECT p.*, n.mark AS neighbor_mark_t, n.issuer_name AS neighbor_name, n.sector AS neighbor_sector,
               n.total_cost AS neighbor_cost, n.n_lenders AS neighbor_lenders,
               fb.first_below AS first_below95_period,
               fb.first_below IS NOT NULL AS hit,
               (fb.first_below IS NOT NULL OR seen.last_seen >= p.event_period + INTERVAL {OBSERVED_DAYS} DAY) AS observed
        FROM signals.nbr_pairs_raw p
        JOIN signals.nbr_features n ON n.borrower_key = p.neighbor_key AND n.period_end = p.event_period
        LEFT JOIN LATERAL (
            SELECT min(x.period_end) AS first_below FROM signals.nbr_features x
            WHERE x.borrower_key = p.neighbor_key AND x.period_end > p.event_period
              AND x.period_end <= p.event_period + INTERVAL {OUTCOME_DAYS} DAY AND x.mark < {OUTCOME_MARK}
        ) fb ON TRUE
        LEFT JOIN LATERAL (
            SELECT max(x.period_end) AS last_seen FROM signals.nbr_features x
            WHERE x.borrower_key = p.neighbor_key AND x.period_end > p.event_period
              AND x.period_end <= p.event_period + INTERVAL {OUTCOME_DAYS} DAY
        ) seen ON TRUE
        """
    )
    con.execute("DROP TABLE signals.nbr_pairs_raw")
    log(f"  neighbour pairs: {len(pairs):,}")
    return len(pairs)


def _test(con: duckdb.DuckDBPyConnection, log) -> None:
    # only events old enough for the whole window to be observable; otherwise the only pairs
    # that count are the ones that already fell, which would inflate every hit rate
    rows = con.execute(
        f"""
        SELECT borrower_key, event_period, is_control, same_sector, hit
        FROM signals.nbr_pairs WHERE observed
          AND event_period <= (SELECT max(period_end) FROM signals.nbr_features) - INTERVAL {OBSERVED_DAYS} DAY
        """
    ).fetchall()
    rng = random.Random(5)
    by_event: dict = {}
    for k, q, ctrl, same, hit in rows:
        by_event.setdefault((k, q), []).append((ctrl, same, hit))
    events = list(by_event)

    def rates(sample) -> tuple[float | None, float | None, int, int]:
        n_hit = n = c_hit = c = 0
        for ev in sample:
            for ctrl, _, hit in by_event[ev]:
                if ctrl:
                    c += 1
                    c_hit += hit
                else:
                    n += 1
                    n_hit += hit
        return (n_hit / n if n else None, c_hit / c if c else None, n, c)

    out: list[tuple] = []
    nb, ct, n_nb, n_ct = rates(events)
    if nb is not None and ct is not None:
        diffs = []
        for _ in range(BOOTSTRAP):
            s = [events[rng.randrange(len(events))] for _ in events]
            a, b, _, _ = rates(s)
            if a is not None and b is not None:
                diffs.append(a - b)
        diffs.sort()
        lo, hi = diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]
        out.append(("all events", len(events), n_nb, nb, n_ct, ct, nb - ct, lo, hi, nb / ct if ct else None))
        log(f"  neighbours {nb:.1%} vs controls {ct:.1%} fell below 0.95 within 4 quarters "
            f"({len(events):,} events; diff {nb - ct:+.1%}, 95% CI {lo:+.1%} to {hi:+.1%})")
    # same-sector neighbours only, against the same events' controls
    sec_events = [ev for ev in events if any(same for _, same, _ in by_event[ev])]
    if sec_events:
        n_hit = n = c_hit = c = 0
        for ev in sec_events:
            for ctrl, same, hit in by_event[ev]:
                if ctrl:
                    c += 1
                    c_hit += hit
                elif same:
                    n += 1
                    n_hit += hit
        if n and c:
            out.append(("events with a known sector: same-sector neighbours", len(sec_events), n, n_hit / n, c, c_hit / c,
                        n_hit / n - c_hit / c, None, None, (n_hit / n) / (c_hit / c) if c_hit else None))
    # by event year
    for yr in sorted({ev[1].year for ev in events}):
        sub = [ev for ev in events if ev[1].year == yr]
        a, b, n, c = rates(sub)
        if a is not None and b is not None:
            out.append((f"events in {yr}", len(sub), n, a, c, b, a - b, None, None, a / b if b else None))
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.nbr_test (
            group_name VARCHAR, n_events INTEGER, n_neighbor_pairs INTEGER, neighbor_hit_rate DOUBLE,
            n_control_pairs INTEGER, control_hit_rate DOUBLE, diff DOUBLE, ci_lo DOUBLE, ci_hi DOUBLE, ratio DOUBLE
        )
        """
    )
    con.executemany("INSERT INTO signals.nbr_test VALUES (?,?,?,?,?,?,?,?,?,?)", out)


def _watchlist(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE signals.nbr_watchlist AS
        WITH latest AS (SELECT max(period_end) AS d FROM signals.nbr_features),
        holders AS (
            SELECT q.issuer_norm AS borrower_key, q.period_end,
                   string_agg(DISTINCT coalesce(m.ticker, m.name), ', ' ORDER BY coalesce(m.ticker, m.name)) AS holders,
                   count(DISTINCT q.cik) FILTER (WHERE m.is_public) AS n_public_holders
            FROM signals.loan_quarter q JOIN ref.bdc_master m USING (cik)
            WHERE q.is_debt AND q.data_ok GROUP BY 1, 2
        ),
        now AS (
            SELECT borrower_key, arg_max(mark, period_end) AS mark_now, max(period_end) AS seen_now
            FROM signals.nbr_features GROUP BY 1
        )
        SELECT p.borrower_key AS event_key, e.issuer_name AS event_name, p.event_period, e.event_mark, e.event_nonaccrual,
               e.sector AS event_sector,
               p.neighbor_key, p.neighbor_name, p.neighbor_sector, p.rank, p.distance, p.same_sector,
               p.neighbor_mark_t, n.mark_now, n.seen_now, p.neighbor_cost, p.neighbor_lenders,
               h.holders, h.n_public_holders
        FROM signals.nbr_pairs p
        JOIN signals.nbr_events e ON e.borrower_key = p.borrower_key AND e.event_period = p.event_period
        JOIN now n ON n.borrower_key = p.neighbor_key
        LEFT JOIN holders h ON h.borrower_key = p.neighbor_key AND h.period_end = p.event_period
        WHERE NOT p.is_control AND p.event_period >= (SELECT d FROM latest) - INTERVAL 200 DAY
        ORDER BY p.event_period DESC, p.borrower_key, p.rank
        """
    )
