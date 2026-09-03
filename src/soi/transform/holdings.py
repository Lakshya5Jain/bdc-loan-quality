"""Build core.filings and core.holdings: one row per (BDC, period end, holding)."""
from __future__ import annotations

import duckdb
import polars as pl

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

# A footnote marks a holding as non-accrual when it is a short, specific note (not boilerplate
# such as "unless otherwise indicated, no investment is on non-accrual").
# a non-zero PIK rate written next to "PIK" ("4.42% PIK", "PIK 2.50%", "SOFR + 5.00% (2.00% PIK)")
PIK_TEXT_RE = (
    r"(?i)(([1-9]\d*(\.\d+)?|0\.\d*[1-9]\d*)\s?%\s*(cash\s*/\s*)?PIK\b"
    r"|\bPIK[^%\d]{0,10}([1-9]\d*(\.\d+)?|0\.\d*[1-9]\d*)\s?%)"
)
NONACCRUAL_RE = r"(?i)non[- ]?accrual"
NONACCRUAL_NEG_RE = (
    r"(?i)(unless otherwise|not on non|no longer|removed from|other than|except|"
    r"none of|was not|were not|are not|is not|do not|does not|not been placed)"
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
        if sum(fvs) <= 1.05 * total:
            continue
        k = len(sigs)
        best: tuple[float, int, int] | None = None  # (abs error, -kept_n, mask)
        if k <= 16:
            for mask in range(1, 1 << k):
                kept = sum(fvs[i] for i in range(k) if mask >> i & 1)
                if not (0.95 * total <= kept <= 1.03 * total):
                    continue
                kept_n = sum(ns[i] for i in range(k) if mask >> i & 1)
                cand = (abs(kept - total), -kept_n, mask)
                if best is None or cand < best:
                    best = cand
        else:
            order = sorted(range(k), key=lambda i: (-ns[i], -fvs[i]))
            mask, kept = 0, 0.0
            for i in order:
                if kept + fvs[i] <= 1.03 * total:
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
               coalesce(fair_value, fair_value_alt) AS fair_value,
               coalesce(cost, cost_alt, cost_alt2) AS cost,
               coalesce(spread, spread_alt) AS spread,
               coalesce(rate, rate_alt, rate_alt2) AS rate,
               coalesce(pik_rate, pik_alt, pik_alt2, pik_alt3) AS pik_rate,
               coalesce(shares, units_alt) AS shares,
               {SIG_EXPR} AS sig
        FROM pivot_all WHERE jv_entity = ''
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE core.holdings_jv AS
        SELECT f.cik, a.ddate AS period_end, a.adsh, a.jv_entity, a.identifier,
               a.fair_value, a.cost, a.principal, a.rate, a.spread, a.pik_rate
        FROM pivot_all a JOIN core.filings f USING (adsh) WHERE a.jv_entity <> ''
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

    # Issuer-level subtotal rows: an identifier that is a prefix (at a separator) of other
    # identifiers in the same filing/period and whose fair value equals the children's sum.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE subtotal_rows AS
        SELECT p.adsh, p.ddate, p.identifier, p.legal_entity
        FROM pivot_num p
        JOIN (
            SELECT c.adsh, c.ddate, c.legal_entity, ip.parent AS identifier,
                   sum(c.fair_value) AS child_fv, sum(c.cost) AS child_cost, count(*) AS n_child
            FROM pivot_num c JOIN ident_parents ip ON ip.identifier = c.identifier
            GROUP BY 1, 2, 3, 4
        ) ch ON ch.adsh = p.adsh AND ch.ddate = p.ddate AND ch.identifier = p.identifier
            AND ch.legal_entity = p.legal_entity
        WHERE (p.fair_value IS NOT NULL AND ch.child_fv IS NOT NULL
               AND abs(p.fair_value - ch.child_fv) <= 0.01 * greatest(abs(p.fair_value), 1))
           OR (p.fair_value IS NULL AND p.cost IS NOT NULL AND ch.child_cost IS NOT NULL
               AND abs(p.cost - ch.child_cost) <= 0.01 * greatest(abs(p.cost), 1))
        """
    )
    # Issuer-level total rows written in a different format from the instrument rows
    # (e.g. "PennantPark Senior Loan Fund, LLC" vs "Investments in ... Issuer Name PennantPark ...").
    # A row is a subtotal when its fair value equals the sum of the *other* rows of the same
    # issuer in the same filing/period and it carries no instrument-level facts of its own.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE issuer_sum_rows AS
        WITH h AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.cost,
                   p.issuer_norm,
                   (n.principal IS NULL AND n.rate IS NULL AND n.spread IS NULL
                    AND n.shares IS NULL) AS bare
            FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
            WHERE p.issuer_norm IS NOT NULL AND p.issuer_norm <> ''
              AND NOT EXISTS (SELECT 1 FROM subtotal_rows s WHERE s.adsh = n.adsh
                              AND s.ddate = n.ddate AND s.identifier = n.identifier
                              AND s.legal_entity = n.legal_entity)
        ),
        agg AS (
            SELECT adsh, ddate, legal_entity, issuer_norm, sum(fair_value) AS tot_fv,
                   sum(cost) AS tot_cost, count(*) AS n
            FROM h GROUP BY 1, 2, 3, 4
        )
        SELECT h.adsh, h.ddate, h.identifier, h.legal_entity
        FROM h JOIN agg a ON a.adsh = h.adsh AND a.ddate = h.ddate
             AND a.legal_entity = h.legal_entity AND a.issuer_norm = h.issuer_norm
        WHERE a.n >= 2 AND h.bare AND h.fair_value IS NOT NULL AND h.fair_value > 0
          AND (abs(h.fair_value - (a.tot_fv - h.fair_value)) <= 0.01 * h.fair_value
               OR EXISTS (SELECT 1 FROM h o WHERE o.adsh = h.adsh AND o.ddate = h.ddate
                          AND o.legal_entity = h.legal_entity AND o.issuer_norm = h.issuer_norm
                          AND o.identifier <> h.identifier AND NOT o.bare
                          AND abs(o.fair_value - h.fair_value) <= 0.001 * h.fair_value))
        """
    )
    # The same holding tagged under identifier variants ("X", "X 1", "X | Affiliated Issuer"):
    # identical issuer, fair value and cost -> keep the variant carrying the most facts.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE variant_dupes AS
        WITH h AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.cost,
                   p.issuer_norm,
                   regexp_replace(regexp_replace(p.ident_key,
                       '\\s*\\|\\s*(non-?)?affiliated? issuer\\s*$', ''), '\\s+\\d{1,3}$', '') AS base_key,
                   (n.principal IS NOT NULL)::INT + (n.rate IS NOT NULL)::INT
                     + (n.spread IS NOT NULL)::INT + (n.shares IS NOT NULL)::INT
                     + (n.pct_net_assets IS NOT NULL)::INT AS n_facts
            FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
            WHERE n.fair_value IS NOT NULL AND n.fair_value > 0 AND n.cost IS NOT NULL
        ),
        ranked AS (
            SELECT *, row_number() OVER (
                       PARTITION BY adsh, ddate, legal_entity, issuer_norm, base_key, fair_value, cost
                       ORDER BY n_facts DESC, length(identifier) DESC, identifier) AS rn
            FROM h
        )
        SELECT adsh, ddate, identifier, legal_entity FROM ranked WHERE rn > 1
        """
    )
    # Same identifier reported both with and without a LegalEntityAxis member: keep the
    # consolidated (no legal entity) row only.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE le_dupes AS
        SELECT a.adsh, a.ddate, a.identifier, a.legal_entity
        FROM pivot_num a JOIN pivot_num b
          ON a.adsh = b.adsh AND a.ddate = b.ddate AND a.identifier = b.identifier
         AND a.legal_entity <> '' AND b.legal_entity = ''
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE excluded AS
        SELECT adsh, ddate, identifier, legal_entity, 'issuer_subtotal' AS reason FROM subtotal_rows
        UNION ALL
        SELECT adsh, ddate, identifier, legal_entity, 'issuer_sum' FROM issuer_sum_rows
        UNION ALL
        SELECT adsh, ddate, identifier, legal_entity, 'variant_dupe' FROM variant_dupes
        UNION ALL
        SELECT adsh, ddate, identifier, legal_entity, 'legal_entity_dupe' FROM le_dupes
        UNION ALL
        SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, 'total_row'
        FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier WHERE p.is_total_row
        """
    )

    # Some filers embed a JV's schedule in the same contexts as their own (no extra axis).
    # Those rows are written in a different format; when the detail overshoots the reported
    # total, drop whole format clusters (largest clusters kept first) until it reconciles.
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
    clusters = con.execute(
        """
        WITH live AS (
            SELECT n.* FROM pivot_num n
            WHERE NOT EXISTS (SELECT 1 FROM excluded e WHERE e.adsh = n.adsh AND e.ddate = n.ddate
                              AND e.identifier = n.identifier AND e.legal_entity = n.legal_entity)
        )
        SELECT l.adsh, l.ddate, l.sig, sum(l.fair_value) AS fv, count(*) AS n,
               any_value(t.total_fv) AS total_fv
        FROM live l JOIN filing_totals t ON t.adsh = l.adsh AND t.ddate = l.ddate
        WHERE t.total_fv > 0
          AND (t.assets IS NULL OR t.total_fv BETWEEN 0.3 * t.assets AND 1.2 * t.assets)
        GROUP BY 1, 2, 3
        """
    ).pl()
    drops = _choose_cluster_drops(clusters)
    con.register("cluster_drops", drops)
    con.execute(
        """
        INSERT INTO excluded
        SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, 'format_cluster'
        FROM pivot_num n JOIN cluster_drops c ON c.adsh = n.adsh AND c.ddate = n.ddate AND c.sig = n.sig
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
               n.fair_value, n.cost, n.principal, n.shares, n.rate, n.spread, n.floor_rate,
               n.pik_rate, n.cash_rate, n.pct_net_assets, n.unfunded_commitment,
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
               CASE WHEN t.assets > 0 THEN d.detail_fv / t.assets END AS detail_to_assets
        FROM det d LEFT JOIN tot t ON t.adsh = d.adsh AND t.ddate = d.period_end
        """
    )
    for t in ("facts_num", "facts_txt", "pivot_num", "pivot_txt", "footnotes", "footnote_agg",
              "subtotal_rows", "issuer_sum_rows", "variant_dupes", "le_dupes", "excluded",
              "pivot_all", "filing_totals"):
        con.execute(f"DROP TABLE IF EXISTS {t}")

    n, nf, np_ = con.execute(
        "SELECT count(*), count(DISTINCT cik), count(DISTINCT (cik, period_end)) FROM core.holdings"
    ).fetchone()
    return f"core.holdings: {n:,} rows, {nf} BDCs, {np_} BDC-periods"
