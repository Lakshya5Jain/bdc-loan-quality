"""Build core.filings and core.holdings: one row per (BDC, period end, holding)."""
from __future__ import annotations

import duckdb
import polars as pl

from soi.db import table_exists
from soi.transform.parse_identifier import candidate_parents, classify_member, parse_identifier

IDENT_RE = r"InvestmentIdentifierAxis\([^)]*\)=(.*?)\(\)(?:;|$)"
NUM_TAGS = {
    "InvestmentOwnedAtFairValue": "fair_value",
    "InvestmentOwnedAtCost": "cost",
    "InvestmentOwnedBalancePrincipalAmount": "principal",
    "InvestmentOwnedBalanceShares": "shares",
    "InvestmentInterestRate": "rate",
    "InvestmentBasisSpreadVariableRate": "spread",
    "InvestmentInterestRateFloor": "floor_rate",
    "InvestmentInterestRatePaidInKind": "pik_rate",
    "InvestmentInterestRatePaidInCash": "cash_rate",
    "InvestmentOwnedPercentOfNetAssets": "pct_net_assets",
    "InvestmentCompanyFinancialCommitmentToInvesteeFutureAmount": "unfunded_commitment",
    # filer-specific (custom) variants seen across several BDCs
    "InvestmentOwnedAtCostNetOfCapitalizedDiscount": "cost_alt",
    "InvestmentOwnedCost": "cost_alt2",
    "InvestmentOwnedAtFairValueNetOfCapitalizedDiscount": "fair_value_alt",
    "InvestmentsFairValueDisclosure": "fair_value_alt2",
    "InvestmentsBasisSpreadVariableRate": "spread_alt",
    "InvestmentFixedInterestRate": "rate_alt",
    "InvestmentInterestRates": "rate_alt2",
    "InvestmentsInterestRatePaidInKind": "pik_alt",
    "InvestmentPaidInKindRate": "pik_alt2",
    "PreferredEquityInvestmentInterestRatePaidInKind": "pik_alt3",
    "InvestmentOwnedBalanceUnits": "units_alt",
}
TXT_TAGS = {
    "InvestmentMaturityDate": "maturity_raw",
    "InvestmentsMaturityMonthAndYear": "maturity_my_raw",
    "DebtInvestmentMaturityOrDissolutionDate": "maturity2_raw",
    "InvestmentAcquisitionDate": "acquired_raw",
    "InvestmentsOwnedAcquisitionDate": "acquired2_raw",
    "InvestmentVariableInterestRateTypeExtensibleEnumeration": "rate_index_raw",
    "InvestmentTypeExtensibleEnumeration": "type_enum",
    "InvestmentIndustrySectorExtensibleEnumeration": "industry_enum",
    "IndustryName": "industry_name",
    "InvestmentIssuerNameExtensibleEnumeration": "issuer_enum",
    "InvestmentIssuerAffiliationExtensibleEnumeration": "affiliation_enum",
    "InvestmentIssuerGeographicRegionExtensibleEnumeration": "geo_enum",
    "InvestmentNonIncomeProducing": "non_income_producing_raw",
    "InvestmentInterestRateTerms": "rate_terms",
}
MEMBER_AXES = {
    "InvestmentIssuerAffiliationAxis": "affiliation_member",
    "InvestmentTypeAxis": "type_member",
    "IndustrySectorAxis": "industry_member",
    "LegalEntityAxis": "legal_entity_member",
}

# a non-zero PIK rate written next to "PIK" ("4.42% PIK", "PIK 2.50%", "SOFR + 5.00% (2.00% PIK)")
PIK_TEXT_RE = (
    r"(?i)(([1-9]\d*(\.\d+)?|0\.\d*[1-9]\d*)\s?%\s*(cash\s*/\s*)?PIK\b"
    r"|\bPIK[^%\d]{0,10}([1-9]\d*(\.\d+)?|0\.\d*[1-9]\d*)\s?%)"
)

# A footnote marks a holding as non-accrual when it is a short note saying THIS investment is on
# non-accrual (not boilerplate such as "excludes those on non-accrual" or "net of non-accrual
# amounts"). Both regexes are applied per individual footnote.
NONACCRUAL_RE = (
    r"(?i)((was|is|were|are|has been|have been|remain(s|ed)?|currently|placed|put) (on|in) non-?\s?accrual"
    r"|on non-?\s?accrual status|non-?\s?accrual or non-?\s?income|^\W*non-?\s?accrual\b"
    r"|non-?\s?accrual (investment|loan|asset|debt)s?\W*$|placed .{0,40} on non-?\s?accrual"
    r"|non-?\s?accrual status as of)"
)
NONACCRUAL_NEG_RE = (
    r"(?i)(exclud|net of|generally|may be|unless|other than|except|not on non|no longer|removed|"
    r"restored|no income|is recognized|based on the estimated|guaranteed non|were not|was not|"
    r"are not|is not|none of)"
)


def _member_expr(axis: str) -> str:
    return f"nullif(regexp_extract(segments, '{axis}\\([^)]*\\)=([A-Za-z0-9_]+)\\(', 1), '')"


JV_AXES = (
    "InvestmentCompanyNonconsolidatedSubsidiaryAxis",
    "ScheduleOfEquityMethodInvestmentEquityMethodInvesteeNameAxis",
)


def _jv_expr() -> str:
    return "coalesce(" + ", ".join(_member_expr(a) for a in JV_AXES) + ", '')"


# first three words of the identifier, digits removed: groups rows written in the same format
SIG_EXPR = (
    "lower(regexp_replace(regexp_extract(identifier, '^\\s*(\\S+(\\s+\\S+){0,2})', 1), "
    "'[0-9.,%()]+', '', 'g'))"
)


def _choose_cluster_drops(clusters: pl.DataFrame) -> pl.DataFrame:
    """For filings whose detail overshoots the reported total by >5%, pick the subset of
    format clusters whose fair value best matches the total (exact search when few clusters)
    and return the clusters to drop."""
    out: list[dict] = []
    if clusters.is_empty():
        return pl.DataFrame(schema={"adsh": pl.Utf8, "ddate": pl.Date, "sig": pl.Utf8})
    for (adsh, ddate), g in clusters.group_by(["adsh", "ddate"]):
        total = float(g["total_fv"][0])
        sigs = g["sig"].to_list()
        fvs = [float(x or 0.0) for x in g["fv"].to_list()]
        ns = g["n"].to_list()
        if sum(fvs) <= 1.02 * total:
            continue
        k = len(sigs)
        keep_mask = sum(1 << i for i in range(k) if sigs[i] == "__keep__")
        best: tuple[float, int, int] | None = None  # (abs error, -kept_n, mask)
        if k <= 16:
            for mask in range(1, 1 << k):
                if mask & keep_mask != keep_mask:
                    continue
                kept = sum(fvs[i] for i in range(k) if mask >> i & 1)
                if not (0.95 * total <= kept <= 1.03 * total):
                    continue
                kept_n = sum(ns[i] for i in range(k) if mask >> i & 1)
                cand = (abs(kept - total), -kept_n, mask)
                if best is None or cand < best:
                    best = cand
        else:
            order = sorted(range(k), key=lambda i: (sigs[i] != "__keep__", -ns[i], -fvs[i]))
            mask, kept = 0, 0.0
            for i in order:
                if sigs[i] == "__keep__" or kept + fvs[i] <= 1.03 * total:
                    mask |= 1 << i
                    kept += fvs[i]
            if 0.95 * total <= kept:
                best = (abs(kept - total), 0, mask)
        if best is None:
            continue
        mask = best[2]
        for i in range(k):
            if not (mask >> i & 1):
                out.append({"adsh": adsh, "ddate": ddate, "sig": sigs[i]})
    return pl.DataFrame(out, schema={"adsh": pl.Utf8, "ddate": pl.Date, "sig": pl.Utf8})


def build_filings(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE core.filings AS
        SELECT adsh, any_value(cik) AS cik, any_value(name) AS name, any_value(form) AS form,
               any_value(period) AS period, any_value(filed) AS filed, any_value(fy) AS fy,
               any_value(fp) AS fp, any_value(detail) AS detail, any_value(inlineurl) AS inlineurl,
               min(source_file) AS source_file
        FROM raw.sub GROUP BY adsh
        """
    )


def build_holdings(con: duckdb.DuckDBPyConnection) -> str:
    build_filings(con)
    # Rates are decimals (0.0842). Some filers tag them in percent (8.42) or basis points (842).
    con.execute(
        """
        CREATE OR REPLACE TEMP MACRO fix_rate(x) AS
            CASE WHEN x IS NULL THEN NULL
                 WHEN abs(x) > 100 THEN x / 10000
                 WHEN abs(x) > 1 THEN x / 100
                 ELSE x END
        """
    )

    num_cols = ",\n".join(
        f"max(value) FILTER (WHERE tag = '{t}') AS {c}" for t, c in NUM_TAGS.items()
    )
    txt_cols = ",\n".join(
        f"any_value(value) FILTER (WHERE tag = '{t}') AS {c}" for t, c in TXT_TAGS.items()
    )
    member_cols = ",\n".join(f"any_value({_member_expr(a)}) AS {c}" for a, c in MEMBER_AXES.items())

    # 1. facts keyed by (adsh, ddate, identifier, legal entity)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE facts_num AS
        SELECT adsh, ddate, regexp_extract(segments, '{IDENT_RE}', 1) AS identifier,
               coalesce({_member_expr("LegalEntityAxis")}, '') AS legal_entity,
               {_jv_expr()} AS jv_entity,
               segments, tag, value, footnote
        FROM raw.num
        WHERE qtrs = 0 AND segments LIKE '%InvestmentIdentifierAxis%'
          AND tag IN ({", ".join(f"'{t}'" for t in NUM_TAGS)})
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE facts_txt AS
        SELECT adsh, ddate, regexp_extract(segments, '{IDENT_RE}', 1) AS identifier,
               coalesce({_member_expr("LegalEntityAxis")}, '') AS legal_entity,
               {_jv_expr()} AS jv_entity,
               tag, value
        FROM raw.txt
        WHERE segments LIKE '%InvestmentIdentifierAxis%'
          AND tag IN ({", ".join(f"'{t}'" for t in TXT_TAGS)})
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE footnotes AS
        SELECT adsh, ddate, identifier, legal_entity, footnote
        FROM (
            SELECT adsh, ddate, regexp_extract(segments, '{IDENT_RE}', 1) AS identifier,
                   coalesce({_member_expr("LegalEntityAxis")}, '') AS legal_entity,
                   unnest(string_split(footnote, ' | ')) AS footnote
            FROM raw.num WHERE segments LIKE '%InvestmentIdentifierAxis%' AND footlen > 0
            UNION ALL
            SELECT adsh, ddate, regexp_extract(segments, '{IDENT_RE}', 1),
                   coalesce({_member_expr("LegalEntityAxis")}, ''),
                   unnest(string_split(footnote, ' | '))
            FROM raw.txt WHERE segments LIKE '%InvestmentIdentifierAxis%' AND footlen > 0
        )
        WHERE identifier <> '' AND trim(footnote) <> ''
        """
    )
    if table_exists(con, "raw", "ix_footnotes"):
        con.execute(
            """
            INSERT INTO footnotes
            SELECT adsh, period_end AS ddate, identifier, '' AS legal_entity, footnote
            FROM raw.ix_footnotes WHERE trim(footnote) <> ''
            """
        )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE footnote_agg AS
        SELECT adsh, ddate, identifier, legal_entity,
               string_agg(DISTINCT footnote, ' || ') AS footnote_text,
               bool_or(length(footnote) < 400 AND regexp_matches(footnote, ?)
                       AND NOT regexp_matches(footnote, ?)) AS nonaccrual_fn
        FROM footnotes GROUP BY adsh, ddate, identifier, legal_entity
        """,
        [NONACCRUAL_RE, NONACCRUAL_NEG_RE],
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE pivot_all AS
        SELECT adsh, ddate, identifier, legal_entity, jv_entity,
               {num_cols},
               {member_cols},
               count(DISTINCT segments) AS n_segments
        FROM facts_num
        WHERE identifier <> ''
        GROUP BY adsh, ddate, identifier, legal_entity, jv_entity
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE pivot_txt AS
        SELECT adsh, ddate, identifier, legal_entity, {txt_cols}
        FROM facts_txt WHERE identifier <> '' AND jv_entity = ''
        GROUP BY adsh, ddate, identifier, legal_entity
        """
    )
    # Holdings of non-consolidated JVs / senior loan programs are tagged in the notes with an
    # extra axis; keep them apart from the BDC's own schedule.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE pivot_num AS
        SELECT * EXCLUDE (jv_entity, fair_value, cost, spread, rate, pik_rate, shares),
               -- (legal-entity rows are diverted below)
               coalesce(fair_value, fair_value_alt, fair_value_alt2) AS fair_value,
               coalesce(cost, cost_alt, cost_alt2) AS cost,
               coalesce(spread, spread_alt) AS spread,
               coalesce(rate, rate_alt, rate_alt2) AS rate,
               coalesce(pik_rate, pik_alt, pik_alt2, pik_alt3) AS pik_rate,
               coalesce(shares, units_alt) AS shares,
               {SIG_EXPR} AS sig
        FROM pivot_all WHERE jv_entity = '' AND legal_entity = ''
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE core.holdings_jv AS
        SELECT f.cik, a.ddate AS period_end, a.adsh,
               CASE WHEN a.jv_entity <> '' THEN a.jv_entity ELSE a.legal_entity END AS jv_entity,
               a.identifier, a.fair_value, a.cost, a.principal, a.rate, a.spread, a.pik_rate
        FROM pivot_all a JOIN core.filings f USING (adsh)
        WHERE a.jv_entity <> '' OR a.legal_entity <> ''
        """
    )

    # 2. parse identifiers in Python (distinct strings only)
    # industry vocabulary observed in tagged data helps strip industry names from identifiers
    extra_industries = tuple(
        r[0] for r in con.execute(
            "SELECT industry_name FROM (SELECT coalesce(industry_name, regexp_replace(industry_enum, '^.*[#:]', '')) "
            "AS industry_name, count(*) n FROM pivot_txt GROUP BY 1) "
            "WHERE industry_name IS NOT NULL AND length(industry_name) BETWEEN 4 AND 60 AND n >= 5"
        ).fetchall()
    )
    idents = con.execute("SELECT DISTINCT identifier FROM pivot_num").pl()
    parsed_rows, parent_rows = [], []
    for i in idents["identifier"].to_list():
        p = parse_identifier(i, extra_industries)
        parsed_rows.append(
            {
                "identifier": i,
                "issuer_name": p.issuer_name,
                "issuer_norm": p.issuer_norm,
                "instrument_type": p.instrument_type,
                "instrument_subtype": p.instrument_subtype,
                "is_debt_text": p.is_debt,
                "is_total_row": p.is_total_row or p.issuer_name.strip() == "",
                "maturity_text": p.maturity_text,
                "ident_key": p.ident_key,
            }
        )
        parent_rows.extend({"identifier": i, "parent": par} for par in candidate_parents(i))
    parsed = pl.DataFrame(
        parsed_rows,
        schema={
            "identifier": pl.Utf8, "issuer_name": pl.Utf8, "issuer_norm": pl.Utf8,
            "instrument_type": pl.Utf8, "instrument_subtype": pl.Utf8, "is_debt_text": pl.Boolean,
            "is_total_row": pl.Boolean, "maturity_text": pl.Utf8, "ident_key": pl.Utf8,
        },
    )
    parents = pl.DataFrame(parent_rows, schema={"identifier": pl.Utf8, "parent": pl.Utf8})
    con.register("parsed_idents", parsed)
    con.register("ident_parents", parents)

    members = con.execute(
        "SELECT DISTINCT type_member FROM pivot_num WHERE type_member IS NOT NULL "
        "UNION SELECT DISTINCT type_enum FROM pivot_txt WHERE type_enum IS NOT NULL"
    ).fetchall()
    member_rows = []
    for (m,) in members:
        c = classify_member(m)
        if c:
            member_rows.append({"type_member": m, "m_type": c[0], "m_is_debt": c[1]})
    con.register(
        "member_types",
        pl.DataFrame(member_rows, schema={"type_member": pl.Utf8, "m_type": pl.Utf8, "m_is_debt": pl.Boolean}),
    )

    # ---- exclusion rules -------------------------------------------------------------------
    # Every rule appends (adsh, ddate, identifier, legal_entity, reason) to `excluded`. Rules run
    # in order and each one only looks at rows not yet excluded ("live").
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE excluded (
            adsh VARCHAR, ddate DATE, identifier VARCHAR, legal_entity VARCHAR, reason VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP MACRO is_live(a, d, i, l) AS
            NOT EXISTS (SELECT 1 FROM excluded e WHERE e.adsh = a AND e.ddate = d
                        AND e.identifier = i AND e.legal_entity = l)
        """
    )

    # R1. Rows that carry neither cost nor fair value are not holdings (unfunded-commitment
    #     tables, affiliate roll-forwards, footnote prose). Commitments are kept separately.
    con.execute(
        """
        INSERT INTO excluded
        SELECT adsh, ddate, identifier, legal_entity, 'no_values'
        FROM pivot_num WHERE cost IS NULL AND fair_value IS NULL
        """
    )
    # R2. Cost missing and fair value <= 0: the unamortized-fee marks of unfunded commitments
    #     that some filers tag per commitment line (the SOI already totals them).
    con.execute(
        """
        INSERT INTO excluded
        SELECT adsh, ddate, identifier, legal_entity, 'no_cost_nonpositive_fv'
        FROM pivot_num n WHERE cost IS NULL AND fair_value <= 0
          AND is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        """
    )
    # R2b. The same holding tagged twice under identifiers that differ only in punctuation or
    #      spacing ("Investments-non-controlled ..." vs "Investmentsnon-controlled ..."), with
    #      identical fair value and cost: keep one.
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.cost,
                   regexp_replace(lower(n.identifier), '[^a-z0-9]+', '', 'g') AS k
            FROM pivot_num n WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY adsh, ddate, legal_entity, k,
                                         coalesce(fair_value, -1), coalesce(cost, -1)
                                         ORDER BY identifier) AS rn
            FROM live
        )
        SELECT adsh, ddate, identifier, legal_entity, 'punct_dupe' FROM ranked WHERE rn > 1
        """
    )
    # R3. In filings that tag cost on (almost) every holding, a row with fair value but no cost
    #     comes from a note table (affiliate schedules, roll-forwards) and duplicates a holding.
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT * FROM pivot_num n WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        cov AS (
            SELECT adsh, ddate, count(cost) * 1.0 / count(*) AS cost_cov, count(*) AS n
            FROM live GROUP BY 1, 2
        )
        SELECT l.adsh, l.ddate, l.identifier, l.legal_entity, 'fv_only_note_row'
        FROM live l JOIN cov c ON c.adsh = l.adsh AND c.ddate = l.ddate
        WHERE l.cost IS NULL AND l.fair_value > 0 AND c.cost_cov >= 0.8 AND c.n >= 20
          AND l.principal IS NULL AND l.shares IS NULL AND l.rate IS NULL AND l.spread IS NULL
        """
    )
    # R3b. Pipe-format filers ("Issuer | instrument | affiliation"): a row whose identifier has
    #      no instrument segment, for an issuer that also has proper instrument rows, is an
    #      issuer-level aggregate scraped from a note (roll-forwards, "largest investment" prose).
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, p.issuer_norm,
                   position('|' IN n.identifier) > 0 AS piped,
                   p.instrument_type = 'unknown' AS no_instrument_words
            FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
            WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        fmt AS (
            SELECT adsh, ddate, avg(piped::INT) AS piped_share, count(*) AS n
            FROM live GROUP BY 1, 2
        ),
        piped_issuers AS (
            SELECT DISTINCT adsh, ddate, issuer_norm FROM live WHERE piped AND issuer_norm <> ''
        )
        SELECT l.adsh, l.ddate, l.identifier, l.legal_entity, 'unpiped_issuer_row'
        FROM live l
        JOIN fmt f ON f.adsh = l.adsh AND f.ddate = l.ddate
        JOIN piped_issuers pi ON pi.adsh = l.adsh AND pi.ddate = l.ddate AND pi.issuer_norm = l.issuer_norm
        WHERE NOT l.piped AND l.no_instrument_words AND f.piped_share >= 0.9 AND f.n >= 20
        """
    )
    # R4. Total / category rows recognised from the identifier text.
    con.execute(
        """
        INSERT INTO excluded
        SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, 'total_row'
        FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
        WHERE p.is_total_row AND is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
          AND n.principal IS NULL AND n.shares IS NULL AND n.rate IS NULL
        """
    )
    # R5. Same identifier reported both with and without a LegalEntityAxis member: keep the
    #     consolidated (no legal entity) row only.
    con.execute(
        """
        INSERT INTO excluded
        SELECT a.adsh, a.ddate, a.identifier, a.legal_entity, 'legal_entity_dupe'
        FROM pivot_num a JOIN pivot_num b
          ON a.adsh = b.adsh AND a.ddate = b.ddate AND a.identifier = b.identifier
         AND a.legal_entity <> '' AND b.legal_entity = ''
        WHERE is_live(a.adsh, a.ddate, a.identifier, a.legal_entity)
        """
    )
    # R6. Issuer-level subtotal rows: an identifier that is a prefix (at a separator) of other
    #     identifiers in the same filing/period whose fair value AND cost equal the children's
    #     sums. With a single child the parent must carry no instrument-level facts of its own
    #     (otherwise it is a sibling tranche that happens to have the same amounts).
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT * FROM pivot_num n WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        ch AS (
            SELECT c.adsh, c.ddate, c.legal_entity, ip.parent AS identifier,
                   sum(c.fair_value) AS child_fv, sum(c.cost) AS child_cost, count(*) AS n_child,
                   count(c.cost) AS n_child_cost
            FROM live c JOIN ident_parents ip ON ip.identifier = c.identifier
            GROUP BY 1, 2, 3, 4
        )
        SELECT p.adsh, p.ddate, p.identifier, p.legal_entity, 'issuer_subtotal'
        FROM live p
        JOIN ch ON ch.adsh = p.adsh AND ch.ddate = p.ddate AND ch.identifier = p.identifier
               AND ch.legal_entity = p.legal_entity
        WHERE p.fair_value IS NOT NULL AND ch.child_fv IS NOT NULL
          AND abs(p.fair_value - ch.child_fv) <= 0.01 * greatest(abs(p.fair_value), 1)
          AND (p.cost IS NULL OR ch.n_child_cost = 0
               OR abs(p.cost - ch.child_cost) <= 0.01 * greatest(abs(p.cost), 1))
          AND (ch.n_child >= 2
               OR (p.principal IS NULL AND p.rate IS NULL AND p.spread IS NULL
                   AND p.shares IS NULL AND p.pik_rate IS NULL))
        """
    )
    # R7. Issuer-level total rows written in a different format from the instrument rows
    #     ("PennantPark Senior Loan Fund, LLC" vs "Investments in ... Issuer Name PennantPark ...").
    #     A bare row (no instrument facts) whose fair value and cost equal the sums of at least
    #     two other rows of the same issuer.
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT n.*, p.issuer_norm,
                   (n.principal IS NULL AND n.rate IS NULL AND n.spread IS NULL
                    AND n.shares IS NULL AND n.pik_rate IS NULL) AS bare
            FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
            WHERE p.issuer_norm IS NOT NULL AND p.issuer_norm <> ''
              AND is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        agg AS (
            SELECT adsh, ddate, legal_entity, issuer_norm, sum(fair_value) AS tot_fv,
                   sum(cost) AS tot_cost, count(*) AS n, count(cost) AS n_cost
            FROM live GROUP BY 1, 2, 3, 4
        )
        SELECT h.adsh, h.ddate, h.identifier, h.legal_entity, 'issuer_sum'
        FROM live h JOIN agg a ON a.adsh = h.adsh AND a.ddate = h.ddate
             AND a.legal_entity = h.legal_entity AND a.issuer_norm = h.issuer_norm
        WHERE a.n >= 3 AND h.bare AND h.fair_value IS NOT NULL AND h.fair_value > 0
          AND abs(h.fair_value - (a.tot_fv - h.fair_value)) <= 0.01 * h.fair_value
          AND (h.cost IS NULL OR a.n_cost <= 1
               OR abs(h.cost - (a.tot_cost - h.cost)) <= 0.01 * greatest(h.cost, 1))
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE custom_tag_rows AS
        SELECT DISTINCT adsh, ddate, regexp_extract(segments, '{IDENT_RE}', 1) AS identifier, tag
        FROM raw.num
        WHERE version NOT LIKE 'us-gaap%' AND version NOT LIKE 'dei%'
          AND segments LIKE '%InvestmentIdentifierAxis%'
        """
    )
    # R9. Note schedules tagged in the same contexts as the BDC's own schedule (a JV or senior
    #     loan program's portfolio, affiliate roll-forwards) are written in a different shape.
    #     Candidate groups per filing, each dropped only when the live detail overshoots the
    #     reported total by >5% and removing the group brings it closer to 1:
    #       (a) unpiped identifiers in a filing where >=90% are "Issuer | ..." pipe-format
    #       (b) rows lacking both principal and shares where >=95% of the others carry one
    #       (c) rows whose first pipe segment is a repeated section label ("Credit Fund | ...")
    #           other than the filing's dominant label
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE filing_totals AS
        SELECT adsh, ddate,
               max(value) FILTER (WHERE tag = 'InvestmentOwnedAtFairValue') AS total_fv,
               max(value) FILTER (WHERE tag = 'Assets') AS assets
        FROM raw.num
        WHERE tag IN ('InvestmentOwnedAtFairValue', 'Assets') AND qtrs = 0
          AND (segments IS NULL OR segments = '')
        GROUP BY adsh, ddate
        """
    )
    groups = con.execute(
        """
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.principal, n.shares,
                   n.cost, n.sig AS words3, p.issuer_norm,
                   position('|' IN n.identifier) > 0 AS piped,
                   trim(split_part(n.identifier, '|', 1)) AS seg1
            FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
            WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        w3 AS (
            SELECT adsh, ddate, words3, count(*) AS n_w, count(DISTINCT issuer_norm) AS n_iss
            FROM live GROUP BY 1, 2, 3
        ),
        w3dom AS (SELECT adsh, ddate, max(n_w) AS dom_w FROM w3 GROUP BY 1, 2),
        ct AS (
            SELECT c.adsh, c.ddate, c.identifier, c.tag, count(*) OVER (PARTITION BY c.adsh, c.ddate, c.tag) AS n_tag
            FROM custom_tag_rows c
        ),
        ct1 AS (
            SELECT adsh, ddate, identifier, arg_max(tag, n_tag) AS ctag, max(n_tag) AS n_tag
            FROM ct GROUP BY 1, 2, 3
        ),
        stats AS (
            SELECT adsh, ddate, count(*) AS n, avg(piped::INT) AS piped_share,
                   avg((principal IS NOT NULL OR shares IS NOT NULL)::INT) AS ps_share,
                   avg((cost IS NOT NULL)::INT) AS cost_share
            FROM live GROUP BY 1, 2
        ),
        seg AS (
            SELECT adsh, ddate, seg1, count(*) AS n_seg FROM live WHERE piped GROUP BY 1, 2, 3
        ),
        dominant AS (
            SELECT adsh, ddate, arg_max(seg1, n_seg) AS dom_seg, max(n_seg) AS dom_n FROM seg GROUP BY 1, 2
        ),
        tagged AS (
            SELECT l.*, t.total_fv, t.assets,
                   CASE WHEN l.cost IS NULL AND l.fair_value > 0 AND st.cost_share >= 0.8 THEN 'no_cost'
                        WHEN st.piped_share >= 0.6 AND NOT l.piped THEN 'unpiped'
                        WHEN st.ps_share >= 0.75 AND l.principal IS NULL AND l.shares IS NULL THEN 'no_principal_or_shares'
                        WHEN l.piped AND sg.n_seg >= 20 AND d.dom_n >= 20 AND l.seg1 <> d.dom_seg
                             AND sg.n_seg < d.dom_n THEN 'section:' || l.seg1
                        WHEN st.piped_share < 0.5 AND w.n_w >= 20 AND w.n_iss >= 5
                             AND w.n_w < wd.dom_w THEN 'words:' || l.words3
                        WHEN c1.n_tag >= 10 AND c1.n_tag <= 0.6 * st.n THEN 'custom:' || c1.ctag
                        ELSE NULL END AS grp
            FROM live l
            JOIN stats st ON st.adsh = l.adsh AND st.ddate = l.ddate
            JOIN filing_totals t ON t.adsh = l.adsh AND t.ddate = l.ddate
            LEFT JOIN seg sg ON sg.adsh = l.adsh AND sg.ddate = l.ddate AND sg.seg1 = l.seg1
            LEFT JOIN dominant d ON d.adsh = l.adsh AND d.ddate = l.ddate
            LEFT JOIN w3 w ON w.adsh = l.adsh AND w.ddate = l.ddate AND w.words3 = l.words3
            LEFT JOIN w3dom wd ON wd.adsh = l.adsh AND wd.ddate = l.ddate
            LEFT JOIN ct1 c1 ON c1.adsh = l.adsh AND c1.ddate = l.ddate AND c1.identifier = l.identifier
            WHERE st.n >= 20 AND t.total_fv > 0
              AND (t.assets IS NULL OR t.total_fv BETWEEN 0.3 * t.assets AND 1.2 * t.assets)
        )
        SELECT adsh, ddate, coalesce(grp, '__keep__') AS sig, sum(fair_value) AS fv, count(*) AS n,
               any_value(total_fv) AS total_fv
        FROM tagged GROUP BY 1, 2, 3
        """
    ).pl()
    drops = _choose_cluster_drops(groups)
    if not drops.is_empty():
        drops = drops.filter(pl.col("sig") != "__keep__")
    con.register("cluster_drops", drops)
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.principal, n.shares, n.cost,
                   n.fair_value, n.sig AS words3,
                   position('|' IN n.identifier) > 0 AS piped,
                   trim(split_part(n.identifier, '|', 1)) AS seg1
            FROM pivot_num n WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        )
        SELECT DISTINCT l.adsh, l.ddate, l.identifier, l.legal_entity, 'note_schedule'
        FROM live l JOIN cluster_drops c ON c.adsh = l.adsh AND c.ddate = l.ddate
        LEFT JOIN custom_tag_rows cr ON cr.adsh = l.adsh AND cr.ddate = l.ddate AND cr.identifier = l.identifier
        WHERE (c.sig = 'no_cost' AND l.cost IS NULL AND l.fair_value > 0)
           OR (c.sig = 'unpiped' AND NOT l.piped)
           OR (c.sig = 'no_principal_or_shares' AND l.principal IS NULL AND l.shares IS NULL)
           OR (c.sig = 'section:' || l.seg1 AND l.piped)
           OR (c.sig = 'words:' || l.words3)
           OR (c.sig = 'custom:' || cr.tag)
        """
    )

    # Unfunded commitments live in their own table (rows dropped by R1 that carry a commitment).
    con.execute(
        """
        CREATE OR REPLACE TABLE core.commitments AS
        SELECT f.cik, n.ddate AS period_end, n.adsh, n.identifier, p.issuer_name, p.issuer_norm,
               n.unfunded_commitment
        FROM pivot_num n
        JOIN core.filings f ON f.adsh = n.adsh
        LEFT JOIN parsed_idents p ON p.identifier = n.identifier
        WHERE n.cost IS NULL AND n.fair_value IS NULL AND n.unfunded_commitment IS NOT NULL
        """
    )

    # 3. choose one filing per (cik, ddate): prefer the filing whose own period is ddate,
    #    then the latest filed (amendments win), then the one with most holdings.
    con.execute(
        """
        CREATE OR REPLACE TABLE core.period_source AS
        WITH counts AS (
            SELECT adsh, ddate, count(*) AS n_holdings,
                   sum(fair_value) AS sum_fv
            FROM pivot_num n
            WHERE NOT EXISTS (SELECT 1 FROM excluded e WHERE e.adsh = n.adsh AND e.ddate = n.ddate
                              AND e.identifier = n.identifier AND e.legal_entity = n.legal_entity)
            GROUP BY adsh, ddate
        ),
        ranked AS (
            SELECT f.cik, c.ddate AS period_end, c.adsh, f.form, f.filed, f.period,
                   c.n_holdings, c.sum_fv,
                   row_number() OVER (
                       PARTITION BY f.cik, c.ddate
                       ORDER BY (f.period = c.ddate) DESC, f.filed DESC, c.n_holdings DESC
                   ) AS rn
            FROM counts c JOIN core.filings f USING (adsh)
            WHERE c.ddate <= current_date  -- a few filers tag a wrong (future) period date
        )
        SELECT * FROM ranked WHERE rn = 1
        """
    )

    # 4. final holdings table
    con.execute(
        """
        CREATE OR REPLACE TABLE core.holdings AS
        SELECT ps.cik, ps.period_end, n.adsh, ps.form, ps.filed,
               n.identifier, p.ident_key, n.legal_entity,
               p.issuer_name, p.issuer_norm,
               CASE
                   WHEN p.instrument_type <> 'unknown' THEN p.instrument_type
                   WHEN mt.m_type IS NOT NULL THEN mt.m_type
                   WHEN me.m_type IS NOT NULL THEN me.m_type
                   WHEN n.principal IS NOT NULL OR n.rate IS NOT NULL OR n.spread IS NOT NULL
                        OR n.floor_rate IS NOT NULL OR n.pik_rate IS NOT NULL THEN 'debt_other'
                   WHEN n.shares IS NOT NULL THEN 'equity_other'
                   ELSE 'unknown'
               END AS instrument_type,
               p.instrument_subtype,
               CASE
                   WHEN p.instrument_type IN ('first_lien','second_lien','subordinated','structured') THEN TRUE
                   WHEN p.instrument_type IN ('equity','preferred','warrant') THEN FALSE
                   WHEN mt.m_is_debt IS NOT NULL THEN mt.m_is_debt
                   WHEN me.m_is_debt IS NOT NULL THEN me.m_is_debt
                   WHEN n.principal IS NOT NULL OR n.rate IS NOT NULL OR n.spread IS NOT NULL
                        OR n.floor_rate IS NOT NULL OR n.pik_rate IS NOT NULL THEN TRUE
                   WHEN n.shares IS NOT NULL THEN FALSE
                   ELSE p.is_debt_text
               END AS is_debt,
               n.fair_value, n.cost, n.principal, n.shares,
               coalesce(fix_rate(n.rate),
                        fix_rate(n.cash_rate) + fix_rate(n.pik_rate),
                        fix_rate(n.pik_rate)) AS rate,
               fix_rate(n.spread) AS spread, fix_rate(n.floor_rate) AS floor_rate,
               fix_rate(n.pik_rate) AS pik_rate, fix_rate(n.cash_rate) AS cash_rate,
               n.pct_net_assets, n.unfunded_commitment,
               n.rate AS rate_raw,
               coalesce(
                   try_cast(t.maturity_raw AS DATE),
                   try_strptime(t.maturity_raw, '%Y-%m')::DATE,
                   try_strptime(t.maturity_my_raw, '%Y-%m')::DATE,
                   try_cast(t.maturity_my_raw AS DATE),
                   try_cast(t.maturity2_raw AS DATE),
                   try_strptime(t.maturity2_raw, '%Y-%m')::DATE,
                   try_strptime(p.maturity_text, '%m/%d/%Y')::DATE,
                   try_strptime(p.maturity_text, '%m/%d/%y')::DATE,
                   try_strptime(p.maturity_text, '%m/%Y')::DATE,
                   try_strptime(p.maturity_text, '%B %d, %Y')::DATE,
                   try_strptime(p.maturity_text, '%B %Y')::DATE,
                   try_strptime(p.maturity_text, '%b %Y')::DATE,
                   try_strptime(p.maturity_text, '%b. %Y')::DATE
               ) AS maturity,
               coalesce(t.maturity_raw, t.maturity_my_raw, t.maturity2_raw, p.maturity_text) AS maturity_raw,
               regexp_replace(t.type_enum, '^.*:', '') AS type_enum,
               coalesce(t.industry_name, regexp_replace(t.industry_enum, '^.*:', '')) AS industry,
               regexp_replace(t.issuer_enum, '^.*:', '') AS issuer_enum,
               regexp_replace(t.affiliation_enum, '^.*:', '') AS affiliation_enum,
               regexp_replace(t.geo_enum, '^.*:', '') AS geo,
               lower(t.non_income_producing_raw) IN ('true', 'yes', '1') AS non_income_producing,
               t.rate_terms,
               coalesce(try_cast(t.acquired_raw AS DATE), try_cast(t.acquired2_raw AS DATE)) AS acquired,
               regexp_replace(t.rate_index_raw, '^.*:', '') AS rate_index,
               n.affiliation_member, n.type_member, n.industry_member,
               n.n_segments, fa.footnote_text,
               coalesce(fa.nonaccrual_fn, FALSE)
                   OR regexp_matches(n.identifier, '(?i)non[- ]?accrual') AS nonaccrual_flag,
               (coalesce(n.pik_rate, 0) > 0
                    OR regexp_matches(coalesce(fa.footnote_text, '') || ' ' || n.identifier, ?))
                   AS pik_flag,
               CASE WHEN n.cost IS NOT NULL AND n.cost <> 0 THEN n.fair_value / n.cost END AS mark,
               CASE WHEN n.principal IS NOT NULL AND n.principal <> 0 THEN n.fair_value / n.principal END AS fv_to_principal
        FROM pivot_num n
        JOIN core.period_source ps ON ps.adsh = n.adsh AND ps.period_end = n.ddate
        LEFT JOIN pivot_txt t ON t.adsh = n.adsh AND t.ddate = n.ddate
             AND t.identifier = n.identifier AND t.legal_entity = n.legal_entity
        LEFT JOIN parsed_idents p ON p.identifier = n.identifier
        LEFT JOIN member_types mt ON mt.type_member = n.type_member
        LEFT JOIN member_types me ON me.type_member = t.type_enum
        LEFT JOIN footnote_agg fa ON fa.adsh = n.adsh AND fa.ddate = n.ddate
             AND fa.identifier = n.identifier AND fa.legal_entity = n.legal_entity
        WHERE NOT EXISTS (SELECT 1 FROM excluded e WHERE e.adsh = n.adsh AND e.ddate = n.ddate
                          AND e.identifier = n.identifier AND e.legal_entity = n.legal_entity)
        """,
        [PIK_TEXT_RE],
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE core.holdings_excluded AS
        SELECT f.cik, e.ddate AS period_end, e.adsh, e.identifier, e.legal_entity, e.reason,
               n.fair_value, n.cost
        FROM excluded e
        JOIN pivot_num n ON n.adsh = e.adsh AND n.ddate = e.ddate AND n.identifier = e.identifier
             AND n.legal_entity = e.legal_entity
        JOIN core.filings f ON f.adsh = e.adsh
        """
    )

    # Documented cases where the filer's reported total is not comparable to the line items
    # (e.g. FSK's total is net of three netting lines): treat as reconciled.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ref.reconciliation_overrides (cik BIGINT, note VARCHAR)
        """
    )
    con.execute("DELETE FROM ref.reconciliation_overrides")
    con.executemany(
        "INSERT INTO ref.reconciliation_overrides VALUES (?, ?)",
        [(1422183, "FSK: reported total investments is net of three netting lines; line items "
                   "verified against the 10-Q (610 of 610 match)")],
    )
    # 5. reconciliation vs undimensioned total fair value in the same filing
    con.execute(
        """
        CREATE OR REPLACE TABLE core.reconciliation AS
        WITH tot AS (SELECT * FROM filing_totals),
        det AS (
            SELECT cik, period_end, adsh, count(*) AS n_holdings, sum(fair_value) AS detail_fv,
                   count(*) FILTER (WHERE is_debt) AS n_debt,
                   count(*) FILTER (WHERE nonaccrual_flag) AS n_nonaccrual
            FROM core.holdings GROUP BY cik, period_end, adsh
        )
        SELECT d.cik, d.period_end, d.adsh, d.n_holdings, d.n_debt, d.n_nonaccrual,
               d.detail_fv, t.total_fv, t.assets,
               CASE WHEN t.total_fv > 0 THEN d.detail_fv / t.total_fv END AS coverage,
               CASE WHEN t.assets > 0 THEN d.detail_fv / t.assets END AS detail_to_assets,
               o.note AS override_note
        FROM det d LEFT JOIN tot t ON t.adsh = d.adsh AND t.ddate = d.period_end
        LEFT JOIN ref.reconciliation_overrides o ON o.cik = d.cik
        """
    )
    for t in ("facts_num", "facts_txt", "pivot_num", "pivot_txt", "footnotes", "footnote_agg",
              "excluded", "pivot_all", "filing_totals"):
        con.execute(f"DROP TABLE IF EXISTS {t}")

    n, nf, np_ = con.execute(
        "SELECT count(*), count(DISTINCT cik), count(DISTINCT (cik, period_end)) FROM core.holdings"
    ).fetchone()
    return f"core.holdings: {n:,} rows, {nf} BDCs, {np_} BDC-periods"
