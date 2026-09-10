"""Build core.filings and core.holdings: one row per (BDC, period end, holding)."""
from __future__ import annotations

import duckdb
import polars as pl

from soi.db import table_exists
from soi.transform.parse_identifier import (
    CORP_SUFFIX_RE,
    candidate_parents,
    classify_member,
    head_stripped_words,
    parse_identifier,
)

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
    "InvestmentInterestBasisSpreadVariableRate": "spread_alt2",
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
# tags whose values are amounts: identical identifiers tagged in several contexts (tranches that
# share one description) are summed over distinct values; rates and percentages are not.
AMOUNT_COLS = {
    "fair_value", "cost", "principal", "shares", "unfunded_commitment",
    "cost_alt", "cost_alt2", "fair_value_alt", "fair_value_alt2", "units_alt",
}
FV_TAGS = ("InvestmentOwnedAtFairValue", "InvestmentOwnedAtFairValueNetOfCapitalizedDiscount",
           "InvestmentsFairValueDisclosure")
COST_TAGS = ("InvestmentOwnedAtCost", "InvestmentOwnedAtCostNetOfCapitalizedDiscount",
             "InvestmentOwnedCost")
# facts tagged in a foreign currency duplicate the USD facts of the same holding (TSLX tags the
# local-currency par of European loans as a second fair value)
USD_ONLY = "NOT (regexp_matches(uom, '^[A-Z]{3}$') AND uom <> 'USD')"
# Some filers (Horizon, Kayne Anderson BDC, Oxford Square, PhenixFIN, Palmer Square, Saratoga,
# SuRo, TriplePoint until 2025) tag each holding as a combination of explicit axis members
# (issuer member x instrument member x industry member) instead of the typed identifier axis.
# Rows carrying these axes are valuation / disclosure tables, never holdings.
MEMBER_SKIP_AXES = (
    "FairValueBy|ValuationTechniqueAxis|MeasurementInputTypeAxis|RangeAxis|ConcentrationRisk|"
    "IncomeStatementLocationAxis|BalanceSheetLocationAxis|StatementScenarioAxis|"
    "FairValueMeasurement|DerivativeInstrumentRiskAxis|AwardTypeAxis|PlanNameAxis"
)
# a filing-period uses member-axis rows when it has fewer identifier-axis rows than this
MEMBER_MODE_MAX_IDENT = 10
MEMBER_MIN_LEAVES = 5
# Axes whose members are categories, never issuers (industry x type x affiliation breakdowns sum
# to the reported total, so they must be kept out before the reconciliation search).
MEMBER_CATEGORY_AXES = (
    "InvestmentTypeAxis", "FinancialInstrumentAxis", "InvestmentIssuerAffiliationAxis",
    "EquitySecuritiesByIndustryAxis", "LienCategoryAxis",
    "EntitySectorIndustryClassificationsSectorAxis", "ConsolidatedEntitiesAxis",
    "StatementGeographicalAxis", "StatementClassOfStockAxis", "InvestmentSecondaryCategorizationAxis",
    "CollateralAxis", "CreditFacilityAxis", "StatementEquityComponentsAxis", "InvestmentHoldingsAxis",
    "FinancingReceivableRecordedInvestmentByClassOfFinancingReceivableAxis", "UnderlyingAssetClassAxis",
    "ExtinguishmentOfDebtAxis", "ProductOrServiceAxis", "RateTypeAxis", "VariableRateAxis",
    "InvestmentsAxis", "TypeOfInvestmentAxis", "AwardTypeAxis", "PlanNameAxis",
)
# axes some filers use for the issuer and others for a category (Oxford Square and Princeton name
# the borrower on RelatedPartyTransactionsByRelatedPartyAxis): issuer-like only when nearly one
# member per row
MEMBER_MIXED_AXES = (
    "RelatedPartyTransactionsByRelatedPartyAxis", "RelatedPartyTransactionAxis", "LongtermDebtTypeAxis",
    "DebtInstrumentAxis",
)
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
        if k > 16:
            # too many clusters for the exact search: fold the smallest (by fair value) into
            # __keep__ so that at most 15 stay separately droppable
            idx = sorted(range(k), key=lambda i: (sigs[i] == "__keep__", -abs(fvs[i])))
            fold = set(idx[15:])
            keep_fv = sum(fvs[i] for i in range(k) if sigs[i] == "__keep__" or i in fold)
            keep_n = sum(ns[i] for i in range(k) if sigs[i] == "__keep__" or i in fold)
            rest = [i for i in range(k) if sigs[i] != "__keep__" and i not in fold]
            sigs = ["__keep__"] + [sigs[i] for i in rest]
            fvs = [keep_fv] + [fvs[i] for i in rest]
            ns = [keep_n] + [ns[i] for i in rest]
            k = len(sigs)
        keep_mask = sum(1 << i for i in range(k) if sigs[i] == "__keep__")
        best: tuple[float, int, int] | None = None  # (abs error, -kept_n, mask)
        # exact band first; when nothing lands there, accept a wider one (filers whose
        # tagged detail is chronically a few percent short of the reported total)
        for lo, hi in ((0.95, 1.03), (0.85, 1.05), (0.8, 1.15)):
            for mask in range(1, 1 << k):
                if mask & keep_mask != keep_mask:
                    continue
                kept = sum(fvs[i] for i in range(k) if mask >> i & 1)
                if not (lo * total <= kept <= hi * total):
                    continue
                kept_n = sum(ns[i] for i in range(k) if mask >> i & 1)
                cand = (abs(kept - total), -kept_n, mask)
                if best is None or cand < best:
                    best = cand
            if best is not None:
                break
        if best is None:
            # no combination reconciles: still drop any single group worth more than half the
            # reported total (a category subtotal or a note-level schedule), never the core rows
            for i in range(k):
                if sigs[i] != "__keep__" and fvs[i] >= 0.5 * total:
                    out.append({"adsh": adsh, "ddate": ddate, "sig": sigs[i]})
            continue
        mask = best[2]
        for i in range(k):
            if not (mask >> i & 1):
                out.append({"adsh": adsh, "ddate": ddate, "sig": sigs[i]})
    return pl.DataFrame(out, schema={"adsh": pl.Utf8, "ddate": pl.Date, "sig": pl.Utf8})


def _build_filing_totals(con: duckdb.DuckDBPyConnection) -> None:
    """Reported total investments per filing/period (temp tables total_candidates, filing_totals)."""
    # Candidates, in order of preference: the undimensioned schedule total; the undimensioned
    # InvestmentsFairValueDisclosure; a single-axis fact whose member is a "total investments"
    # member (KBDC, PhenixFIN, Princeton tag the total only that way); the sum of one axis's
    # members for affiliation / ownership-bucket axes (SLR, Oxford Square).
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE total_candidates AS
        WITH a AS (
            SELECT adsh, ddate, max(value) AS assets FROM raw.num
            WHERE tag = 'Assets' AND qtrs = 0 AND (segments IS NULL OR segments = '') GROUP BY 1, 2
        ),
        undim AS (
            SELECT adsh, ddate, value,
                   CASE WHEN tag = 'InvestmentOwnedAtFairValue' THEN 1 ELSE 2 END AS prio,
                   'undimensioned ' || tag AS source
            FROM raw.num
            WHERE tag IN ('InvestmentOwnedAtFairValue', 'InvestmentsFairValueDisclosure') AND qtrs = 0
              AND (segments IS NULL OR segments = '') AND {USD_ONLY}
        ),
        one_axis AS (
            SELECT adsh, ddate, value, tag,
                   regexp_extract(segments, '^([A-Za-z]+Axis)\\(', 1) AS axis,
                   regexp_extract(segments, '=([A-Za-z0-9_]+)\\(', 1) AS member
            FROM raw.num
            WHERE tag IN ('InvestmentOwnedAtFairValue', 'InvestmentsFairValueDisclosure') AND qtrs = 0
              AND dimn = 1 AND {USD_ONLY}
        ),
        total_member AS (
            SELECT adsh, ddate, value, 3 AS prio, 'member ' || member AS source
            FROM one_axis
            WHERE regexp_matches(member,
                  '^(Total)?((Portfolio|Securities)?Investments?|InvestmentsInSecurities(AndCashEquivalents)?)(AtFairValue)?Member$')
        ),
        axis_sum AS (
            SELECT adsh, ddate, sum(value) AS value, 4 AS prio, 'sum over ' || axis AS source
            FROM (SELECT DISTINCT adsh, ddate, tag, axis, member, value FROM one_axis
                  WHERE axis IN ('InvestmentIssuerAffiliationAxis', 'InvestmentHoldingsAxis',
                                 'ConsolidatedEntitiesAxis', 'InvestmentTypeAxis')
                    AND NOT regexp_matches(member, '(?i)total|cash'))
            GROUP BY adsh, ddate, tag, axis HAVING count(*) BETWEEN 2 AND 6
        ),
        cand AS (
            SELECT * FROM undim UNION ALL SELECT * FROM total_member UNION ALL SELECT * FROM axis_sum
        )
        SELECT c.adsh, c.ddate, c.value, c.prio, c.source, a.assets,
               a.assets > 0 AND c.value BETWEEN 0.3 * a.assets AND 1.2 * a.assets AS in_band
        FROM cand c LEFT JOIN a USING (adsh, ddate)
        WHERE c.value > 0
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE filing_totals AS
        WITH a AS (
            SELECT adsh, ddate, max(value) AS assets FROM raw.num
            WHERE tag = 'Assets' AND qtrs = 0 AND (segments IS NULL OR segments = '') GROUP BY 1, 2
        ),
        best AS (
            SELECT adsh, ddate,
                   coalesce(
                       arg_min(value, abs(value / assets - 1) + prio * 0.001) FILTER (WHERE in_band),
                       max(value) FILTER (WHERE (assets IS NULL OR assets <= 0) AND prio = 1)
                   ) AS total_fv,
                   coalesce(
                       arg_min(source, abs(value / assets - 1) + prio * 0.001) FILTER (WHERE in_band),
                       arg_max(source, value) FILTER (WHERE (assets IS NULL OR assets <= 0) AND prio = 1)
                   ) AS total_source
            FROM total_candidates GROUP BY 1, 2
        )
        SELECT adsh, ddate, b.total_fv, b.total_source, a.assets FROM best b FULL JOIN a USING (adsh, ddate)
        """
    )


def _camel_expr(col: str) -> str:
    """SQL: 'ArborWorksHoldcoLLCClassAUnitsMember' -> 'Arbor Works Holdco LLC Class A Units'."""
    x = f"regexp_replace({col}, 'Member$', '')"
    x = f"regexp_replace({x}, '([a-z0-9])([A-Z])', '\\1 \\2', 'g')"
    x = f"regexp_replace({x}, '([A-Z])([A-Z][a-z])', '\\1 \\2', 'g')"
    x = f"regexp_replace({x}, '([A-Za-z])([0-9])', '\\1 \\2', 'g')"
    return f"trim(regexp_replace({x}, '_', ' ', 'g'))"


_PHRASE_STOP = frozenset({
    "united", "states", "canada", "debt", "equity", "investments", "investment", "securities",
    "portfolio", "company", "companies", "senior", "secured", "first", "second", "lien", "non",
    "control", "controlled", "affiliate", "affiliated", "unaffiliated", "loan", "loans", "notes",
    "term", "total", "sub", "subtotal", "warrants", "warrant", "preferred", "common", "stock",
    "type", "of", "in", "issuer", "name", "industry", "level", "cash", "equivalents",
})


def _learn_industry_phrases(rows: list[tuple[int, str]]) -> list[str]:
    """Industry names that filers write between the category heads and the issuer without
    tagging them (Trinity: "... United States Space Technology Rocket Lab USA, Inc. ...").
    A leading phrase of 1-5 words is an industry when, within one BDC, it precedes at least
    three different issuers."""
    from collections import defaultdict

    tails: dict[tuple[int, str], set[str]] = defaultdict(set)
    for cik, ident in rows:
        words = head_stripped_words(ident)
        for k in range(1, min(6, len(words) - 1)):
            prefix = " ".join(words[:k])
            if CORP_SUFFIX_RE.search(prefix) or not prefix[0].isalpha() or not prefix[0].isupper():
                continue
            if prefix.endswith(",") or prefix.split()[-1].lower() in ("and", "&", "of", "the"):
                continue
            if any(w.lower().strip(",") in _PHRASE_STOP for w in prefix.split()):
                continue
            tails[(cik, prefix)].add(" ".join(words[k:]))
    learned = {prefix for (_, prefix), t in tails.items() if len(t) >= 3}
    return sorted(learned, key=len, reverse=True)


def _apply_ix_facts(con: duckdb.DuckDBPyConnection) -> None:
    """Where facts read from the filing itself (raw.ix_facts, see `soi ixfacts`) list more
    holdings than the bulk data set for a filing-period, replace the bulk facts of that
    filing-period with them. GSBD's bulk rows are 8-12% short every quarter since 2025."""
    con.execute("CREATE OR REPLACE TEMP TABLE ix_override (adsh VARCHAR, ddate DATE)")
    if not table_exists(con, "raw", "ix_facts"):
        return
    fv = ", ".join(f"'{t}'" for t in FV_TAGS)
    con.execute(
        f"""
        INSERT INTO ix_override
        WITH ix AS (
            SELECT adsh, ddate, count(DISTINCT identifier) AS n_ix FROM raw.ix_facts
            WHERE tag IN ({fv}) AND uom = 'USD' GROUP BY 1, 2
        ),
        bulk AS (
            SELECT adsh, ddate, count(DISTINCT identifier) AS n_bulk FROM facts_num
            WHERE tag IN ({fv}) AND identifier <> '' GROUP BY 1, 2
        )
        SELECT ix.adsh, ix.ddate FROM ix LEFT JOIN bulk USING (adsh, ddate)
        WHERE ix.n_ix > 1.02 * coalesce(bulk.n_bulk, 0) AND ix.n_ix >= 20
        """
    )
    con.execute(
        f"""
        DELETE FROM facts_num WHERE (adsh, ddate) IN (SELECT (adsh, ddate) FROM ix_override);
        INSERT INTO facts_num
        SELECT x.adsh, x.ddate, x.identifier, '' AS legal_entity, '' AS jv_entity,
               'InvestmentIdentifierAxis(ix)=' || x.identifier || '()' AS segments,
               x.tag, x.value, NULL AS footnote
        FROM raw.ix_facts x JOIN ix_override o USING (adsh, ddate)
        WHERE x.value IS NOT NULL AND x.tag IN ({", ".join(f"'{t}'" for t in NUM_TAGS)})
          AND NOT (regexp_matches(x.uom, '^[A-Z]{{3}}$') AND x.uom <> 'USD')
        """
    )


def _build_member_axis_rows(con: duckdb.DuckDBPyConnection) -> None:
    """Holdings tagged as explicit axis-member combinations (temp tables member_ident,
    facts_member). A row is a leaf when no other row of the filing-period carries a strict
    superset of its members (category subtotals are subsets of their holdings). The leaf's
    identifier is its member names, the axis with the most distinct members first (the issuer
    axis), so that the text parser sees "Issuer | Instrument | Industry".

    Member rows are used in two ways: as the whole schedule when the filing-period has almost no
    identifier-axis rows ('member'), or as fill-in rows whose fair value matches no
    identifier-axis row ('member_fill', e.g. Horizon's warrants). The reconciliation-gated
    cluster search (R9) drops leftover category rows by axis set."""
    fv_cost = ", ".join(f"'{t}'" for t in FV_TAGS + COST_TAGS)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE member_ctx AS
        SELECT adsh, ddate, segments,
               list_sort(list_filter(string_split(regexp_replace(segments, '\\([^)]*\\)', '', 'g'), ';'),
                                     x -> x <> '')) AS toks,
               max(value) FILTER (WHERE tag IN ({", ".join(f"'{t}'" for t in FV_TAGS)})) AS fv
        FROM raw.num
        WHERE qtrs = 0 AND dimn > 0 AND tag IN ({fv_cost})
          AND segments NOT LIKE '%InvestmentIdentifierAxis%'
          AND NOT regexp_matches(segments, '{MEMBER_SKIP_AXES}')
          AND {USD_ONLY}
        GROUP BY adsh, ddate, segments
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE member_leaf AS
        WITH sup AS (
            SELECT a.adsh, a.ddate, a.segments, a.fv, b.fv AS child_fv, len(b.toks) AS depth
            FROM member_ctx a JOIN member_ctx b ON a.adsh = b.adsh AND a.ddate = b.ddate
                 AND len(b.toks) > len(a.toks) AND list_has_all(b.toks, a.toks)
        ),
        -- immediate children: the supersets at the shallowest depth below the row
        agg AS (
            SELECT adsh, ddate, segments, any_value(fv) AS fv,
                   sum(child_fv) FILTER (WHERE depth = mind) AS child_sum
            FROM (SELECT *, min(depth) OVER (PARTITION BY adsh, ddate, segments) AS mind FROM sup)
            GROUP BY 1, 2, 3
        ),
        -- a row with supersets is a subtotal when its fair value equals its children's sum
        -- (SuRo tags a company's common stock beside its share-class rows: that one stays)
        parents AS (
            SELECT adsh, ddate, segments FROM agg
            WHERE fv IS NULL OR child_sum IS NULL OR abs(fv - child_sum) <= 0.02 * greatest(abs(fv), 1)
               OR fv > child_sum
        )
        SELECT m.* FROM member_ctx m ANTI JOIN parents USING (adsh, ddate, segments)
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE member_ident AS
        WITH ic AS (
            SELECT adsh, ddate, count(DISTINCT identifier) AS n_ident,
                   list(DISTINCT value) AS ident_fvs
            FROM facts_num
            WHERE tag IN ({", ".join(f"'{t}'" for t in FV_TAGS)}) AND identifier <> ''
              AND legal_entity = '' AND jv_entity = ''
            GROUP BY 1, 2
        ),
        lc AS (SELECT adsh, ddate, count(*) FILTER (WHERE fv IS NOT NULL) AS n_leaf FROM member_leaf GROUP BY 1, 2),
        mode AS (
            SELECT l.adsh, l.ddate, ic.ident_fvs,
                   CASE WHEN coalesce(ic.n_ident, 0) < {MEMBER_MODE_MAX_IDENT} AND l.n_leaf >= {MEMBER_MIN_LEAVES}
                             THEN 'member'
                        WHEN ic.n_ident >= {MEMBER_MODE_MAX_IDENT} AND l.n_leaf >= {MEMBER_MIN_LEAVES}
                             AND t.total_fv > 0 THEN 'member_fill' END AS src
            FROM lc l LEFT JOIN ic USING (adsh, ddate)
            LEFT JOIN filing_totals t USING (adsh, ddate)
        ),
        chosen AS (
            SELECT m.adsh, m.ddate, m.segments, m.toks, mo.src
            FROM member_leaf m JOIN mode mo USING (adsh, ddate)
            WHERE mo.src = 'member'
               OR (mo.src = 'member_fill' AND NOT list_contains(mo.ident_fvs, m.fv))
        ),
        tok AS (
            SELECT adsh, ddate, segments, src, unnest(toks) AS tok FROM chosen
        ),
        split AS (
            SELECT adsh, ddate, segments, src, split_part(tok, '=', 1) AS axis, split_part(tok, '=', 2) AS member
            FROM tok
        ),
        axis_card AS (SELECT adsh, ddate, axis, count(DISTINCT member) AS n_members FROM split GROUP BY 1, 2, 3),
        n_rows AS (SELECT adsh, ddate, count(*) AS n_leaf FROM chosen GROUP BY 1, 2),
        -- an axis whose members are nearly one per row names the issuers (or the instruments);
        -- rows without such an axis are category subtotals whatever their depth
        issuer_rows AS (
            SELECT DISTINCT s.adsh, s.ddate, s.segments
            FROM split s JOIN axis_card ac USING (adsh, ddate, axis) JOIN n_rows r USING (adsh, ddate)
            WHERE (ac.n_members >= 3
                   AND ac.axis NOT IN ({", ".join(f"'{a}'" for a in MEMBER_CATEGORY_AXES + MEMBER_MIXED_AXES)}))
               OR (ac.axis IN ({", ".join(f"'{a}'" for a in MEMBER_MIXED_AXES)})
                   AND ac.n_members >= {MEMBER_MIN_LEAVES} AND ac.n_members >= 0.5 * r.n_leaf)
        )
        SELECT s.adsh, s.ddate, s.segments, any_value(s.src) AS src,
               string_agg({_camel_expr("s.member")}, ' | ' ORDER BY ac.n_members DESC, s.axis) AS identifier,
               string_agg(regexp_replace(s.axis, 'Axis$', ''), ',' ORDER BY s.axis) AS axis_set,
               -- JV axes are not used to divert member rows: Saratoga tags its own schedule with
               -- its CLO's member; a JV's own portfolio forms a separate axis set for R9 instead
               '' AS jv_entity
        FROM split s JOIN axis_card ac USING (adsh, ddate, axis)
        JOIN issuer_rows ir USING (adsh, ddate, segments)
        GROUP BY s.adsh, s.ddate, s.segments
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE facts_member AS
        SELECT n.adsh, n.ddate, m.identifier, m.jv_entity, n.segments, n.tag, n.value, n.footnote,
               m.src, m.axis_set
        FROM raw.num n JOIN member_ident m ON m.adsh = n.adsh AND m.ddate = n.ddate AND m.segments = n.segments
        WHERE n.qtrs = 0 AND n.tag IN ({", ".join(f"'{t}'" for t in NUM_TAGS)}) AND {USD_ONLY}
        """
    )


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

    _build_filing_totals(con)
    num_cols = ",\n".join(
        (f"sum(DISTINCT value) FILTER (WHERE tag = '{t}') AS {c}" if c in AMOUNT_COLS
         else f"max(value) FILTER (WHERE tag = '{t}') AS {c}")
        for t, c in NUM_TAGS.items()
    )
    txt_cols = ",\n".join(
        f"any_value(value) FILTER (WHERE tag = '{t}') AS {c}" for t, c in TXT_TAGS.items()
    )
    member_cols = ",\n".join(f"any_value({_member_expr(a)}) AS {c}" for a, c in MEMBER_AXES.items())

    # 1. facts keyed by (adsh, ddate, identifier, legal entity)
    # Some filers put a date or a number on the identifier axis and name the issuer on other
    # axes (TriplePoint 2022: "InvestmentIdentifierAxis=12/30/2021; PortfolioCompaniesAxis=Cartcom
    # Inc"): the members become the identifier, the date its last segment.
    member_text = (
        "array_to_string(list_transform(list_filter(string_split(regexp_replace(segments, '\\([^)]*\\)', '', 'g'), ';'), "
        "x -> x <> '' AND x NOT LIKE 'InvestmentIdentifierAxis=%'), x -> " + _camel_expr("split_part(x, '=', 2)") + "), ' | ')"
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP MACRO ident_text(segments) AS
            CASE WHEN regexp_matches(regexp_extract(segments, '{IDENT_RE}', 1), '^[0-9/. -]{{4,}}$')
                      AND regexp_matches(segments, '[A-Za-z]+Axis\\([^)]*\\)=[A-Za-z0-9_]+Member')
                 THEN {member_text} || ' | ' || regexp_extract(segments, '{IDENT_RE}', 1)
                 ELSE regexp_extract(segments, '{IDENT_RE}', 1) END
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE facts_num AS
        SELECT adsh, ddate, ident_text(segments) AS identifier,
               coalesce({_member_expr("LegalEntityAxis")}, '') AS legal_entity,
               {_jv_expr()} AS jv_entity,
               segments, tag, value, footnote
        FROM raw.num
        WHERE qtrs = 0 AND segments LIKE '%InvestmentIdentifierAxis%'
          AND tag IN ({", ".join(f"'{t}'" for t in NUM_TAGS)})
          AND {USD_ONLY}
        """
    )
    _apply_ix_facts(con)
    _build_member_axis_rows(con)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE facts_txt AS
        SELECT adsh, ddate, ident_text(segments) AS identifier,
               coalesce({_member_expr("LegalEntityAxis")}, '') AS legal_entity,
               {_jv_expr()} AS jv_entity,
               tag, value
        FROM raw.txt
        WHERE segments LIKE '%InvestmentIdentifierAxis%'
          AND tag IN ({", ".join(f"'{t}'" for t in TXT_TAGS)})
        UNION ALL
        SELECT t.adsh, t.ddate, m.identifier, '', m.jv_entity, t.tag, t.value
        FROM raw.txt t JOIN member_ident m ON m.adsh = t.adsh AND m.ddate = t.ddate AND m.segments = t.segments
        WHERE t.tag IN ({", ".join(f"'{t}'" for t in TXT_TAGS)})
        """
    )
    if table_exists(con, "raw", "ix_facts"):
        con.execute(
            f"""
            DELETE FROM facts_txt WHERE (adsh, ddate) IN (SELECT (adsh, ddate) FROM ix_override);
            INSERT INTO facts_txt
            SELECT x.adsh, x.ddate, x.identifier, '', '', x.tag, x.txt
            FROM raw.ix_facts x JOIN ix_override o USING (adsh, ddate)
            WHERE x.txt IS NOT NULL AND x.tag IN ({", ".join(f"'{t}'" for t in TXT_TAGS)})
            """
        )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE footnotes AS
        SELECT adsh, ddate, identifier, legal_entity, footnote
        FROM (
            SELECT adsh, ddate, ident_text(segments) AS identifier,
                   coalesce({_member_expr("LegalEntityAxis")}, '') AS legal_entity,
                   unnest(string_split(footnote, ' | ')) AS footnote
            FROM raw.num WHERE segments LIKE '%InvestmentIdentifierAxis%' AND footlen > 0
            UNION ALL
            SELECT adsh, ddate, ident_text(segments),
                   coalesce({_member_expr("LegalEntityAxis")}, ''),
                   unnest(string_split(footnote, ' | '))
            FROM raw.txt WHERE segments LIKE '%InvestmentIdentifierAxis%' AND footlen > 0
            UNION ALL
            SELECT n.adsh, n.ddate, m.identifier, '', unnest(string_split(n.footnote, ' | '))
            FROM raw.num n JOIN member_ident m ON m.adsh = n.adsh AND m.ddate = n.ddate AND m.segments = n.segments
            WHERE n.footlen > 0
        )
        WHERE identifier <> '' AND trim(footnote) <> ''
        """
    )
    if table_exists(con, "raw", "ix_footnotes"):
        con.execute(
            """
            INSERT INTO footnotes
            SELECT adsh, period_end AS ddate, regexp_replace(identifier, '[–—‑]', '-', 'g'), '' AS legal_entity, footnote
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
               count(DISTINCT segments) AS n_segments,
               'ident' AS src, NULL::VARCHAR AS axis_set
        FROM facts_num
        WHERE identifier <> ''
        GROUP BY adsh, ddate, identifier, legal_entity, jv_entity
        UNION ALL
        SELECT f.adsh, f.ddate, f.identifier, '' AS legal_entity, f.jv_entity,
               {num_cols},
               {member_cols},
               count(DISTINCT segments) AS n_segments,
               any_value(f.src) AS src, any_value(f.axis_set) AS axis_set
        FROM facts_member f
        GROUP BY f.adsh, f.ddate, f.identifier, f.jv_entity
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
               coalesce(spread, spread_alt, spread_alt2) AS spread,
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
            "WHERE industry_name IS NOT NULL AND length(industry_name) BETWEEN 4 AND 60 AND n >= 2"
        ).fetchall()
    )
    learned = _learn_industry_phrases(
        con.execute(
            "SELECT DISTINCT f.cik, n.identifier FROM pivot_num n JOIN core.filings f USING (adsh) "
            "WHERE n.identifier NOT LIKE '%|%' AND length(n.identifier) BETWEEN 20 AND 400"
        ).fetchall()
    )
    extra_industries = tuple(dict.fromkeys(extra_industries + tuple(learned)))
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
                "par_amount": p.par_amount,
                "ident_key": p.ident_key,
            }
        )
        parent_rows.extend({"identifier": i, "parent": par} for par in candidate_parents(i))
    parsed = pl.DataFrame(
        parsed_rows,
        schema={
            "identifier": pl.Utf8, "issuer_name": pl.Utf8, "issuer_norm": pl.Utf8,
            "instrument_type": pl.Utf8, "instrument_subtype": pl.Utf8, "is_debt_text": pl.Boolean,
            "is_total_row": pl.Boolean, "maturity_text": pl.Utf8, "par_amount": pl.Float64,
            "ident_key": pl.Utf8,
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

    # Reported totals per filing/period. Filers tag several undimensioned
    # InvestmentOwnedAtFairValue facts (the schedule total, but also note-level figures such as
    # a single affiliate's total); pick the one closest to total assets when assets are known,
    # and only fall back to the largest value when they are not.
    # ---- unit errors -----------------------------------------------------------------------
    # Some filers scale a handful of rows by 1,000 (fair value and cost tagged in thousands while
    # the rest of the schedule is in dollars). A row whose fair value AND cost are ~1,000x its
    # principal, or whose fair value exceeds the filing's reported total, is a candidate; the
    # scale-down is applied per filing only when it moves the detail sum closer to the total.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE unit_suspects AS
        SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.cost, n.principal,
               t.total_fv,
               (n.principal > 0 AND n.fair_value / n.principal BETWEEN 700 AND 1400
                AND (n.cost IS NULL OR n.cost / n.principal BETWEEN 700 AND 1400)) AS ratio_1000,
               (n.fair_value > 1.05 * t.total_fv) AS above_total
        FROM pivot_num n JOIN filing_totals t ON t.adsh = n.adsh AND t.ddate = n.ddate
        WHERE t.total_fv > 0 AND n.fair_value IS NOT NULL
          AND ((n.principal > 0 AND n.fair_value / n.principal BETWEEN 700 AND 1400
                AND (n.cost IS NULL OR n.cost / n.principal BETWEEN 700 AND 1400))
               OR n.fair_value > 1.05 * t.total_fv)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE unit_fixes AS
        WITH filing AS (
            SELECT n.adsh, n.ddate, sum(n.fair_value) AS sum_fv
            FROM pivot_num n JOIN (SELECT DISTINCT adsh, ddate FROM unit_suspects) u USING (adsh, ddate)
            GROUP BY 1, 2
        ),
        susp AS (
            SELECT adsh, ddate, sum(fair_value) AS susp_fv, any_value(total_fv) AS total_fv
            FROM unit_suspects GROUP BY 1, 2
        ),
        decide AS (
            -- the ratio alone cannot say whether value or principal is off by 1,000; when the
            -- suspect rows by themselves exceed the reported total the values are wrong
            SELECT adsh, ddate, susp_fv > 2.0 * total_fv AS scale_down FROM susp
        )
        SELECT u.adsh, u.ddate, u.identifier, u.legal_entity,
               CASE WHEN u.above_total OR d.scale_down THEN 'value_div_1000' ELSE 'principal_x_1000' END AS fix
        FROM unit_suspects u JOIN decide d USING (adsh, ddate)
        UNION ALL
        SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, 'principal_div_1000'
        FROM pivot_num n
        WHERE n.principal > 0 AND n.fair_value / n.principal BETWEEN 0.0007 AND 0.0014
          AND (n.cost IS NULL OR n.cost / n.principal BETWEEN 0.0007 AND 0.0014)
        """
    )
    # Second pass: in filings with several scaled rows, rows without principal (warrants,
    # equity) that are still individually large are scaled too when that moves the filing
    # sum closer to the reported total.
    con.execute(
        """
        INSERT INTO unit_fixes
        WITH bad AS (
            SELECT adsh, ddate, count(*) AS n_fix FROM unit_fixes WHERE fix = 'value_div_1000'
            GROUP BY 1, 2 HAVING count(*) >= 3
        ),
        cand AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, t.total_fv
            FROM pivot_num n JOIN bad USING (adsh, ddate)
            JOIN filing_totals t ON t.adsh = n.adsh AND t.ddate = n.ddate
            WHERE n.principal IS NULL AND n.fair_value >= 0.05 * t.total_fv
              AND NOT EXISTS (SELECT 1 FROM unit_fixes u WHERE u.adsh = n.adsh AND u.ddate = n.ddate
                              AND u.identifier = n.identifier AND u.legal_entity = n.legal_entity)
        ),
        sums AS (
            SELECT n.adsh, n.ddate, sum(n.fair_value) AS sum_fv
            FROM pivot_num n JOIN bad USING (adsh, ddate) GROUP BY 1, 2
        ),
        fixed AS (
            SELECT adsh, ddate, sum(fair_value) AS fixed_fv FROM pivot_num
            WHERE (adsh, ddate, identifier, legal_entity) IN
                  (SELECT (adsh, ddate, identifier, legal_entity) FROM unit_fixes WHERE fix = 'value_div_1000')
            GROUP BY 1, 2
        ),
        csum AS (SELECT adsh, ddate, sum(fair_value) AS cand_fv, any_value(total_fv) AS total_fv FROM cand GROUP BY 1, 2),
        decide AS (
            SELECT s.adsh, s.ddate,
                   abs((s.sum_fv - f.fixed_fv * 0.999 - c.cand_fv * 0.999) / c.total_fv - 1)
                       < abs((s.sum_fv - f.fixed_fv * 0.999) / c.total_fv - 1) AS go
            FROM sums s JOIN fixed f USING (adsh, ddate) JOIN csum c USING (adsh, ddate)
        )
        SELECT c.adsh, c.ddate, c.identifier, c.legal_entity, 'value_div_1000'
        FROM cand c JOIN decide d USING (adsh, ddate) WHERE d.go
        """
    )
    con.execute(
        """
        UPDATE pivot_num SET fair_value = fair_value / 1000, cost = cost / 1000
        WHERE (adsh, ddate, identifier, legal_entity) IN
              (SELECT (adsh, ddate, identifier, legal_entity) FROM unit_fixes WHERE fix = 'value_div_1000')
        """
    )
    con.execute(
        """
        UPDATE pivot_num SET principal = principal * 1000
        WHERE (adsh, ddate, identifier, legal_entity) IN
              (SELECT (adsh, ddate, identifier, legal_entity) FROM unit_fixes WHERE fix = 'principal_x_1000')
        """
    )
    con.execute(
        """
        UPDATE pivot_num SET principal = principal / 1000
        WHERE (adsh, ddate, identifier, legal_entity) IN
              (SELECT (adsh, ddate, identifier, legal_entity) FROM unit_fixes WHERE fix = 'principal_div_1000')
        """
    )
    # A row with cost but no fair value fact is a holding whose fair value is a dash in the
    # schedule (written down to zero); filers tag the cost and skip the empty cell. Applied only
    # in filings that tag fair value on almost every other row, and never when the fair value
    # exists in a foreign currency.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fx_fv AS
        SELECT DISTINCT adsh, ddate, regexp_extract(segments, '{IDENT_RE}', 1) AS identifier
        FROM raw.num
        WHERE qtrs = 0 AND tag IN ({", ".join(f"'{t}'" for t in FV_TAGS)}) AND uom <> 'USD'
          AND segments LIKE '%InvestmentIdentifierAxis%'
        """
    )
    con.execute(
        """
        UPDATE pivot_num SET fair_value = 0
        WHERE fair_value IS NULL AND cost IS NOT NULL AND src = 'ident'
          AND (adsh, ddate) IN (
              SELECT (adsh, ddate) FROM pivot_num GROUP BY adsh, ddate
              HAVING count(fair_value) >= 0.9 * count(*) AND count(*) >= 20)
          AND NOT EXISTS (SELECT 1 FROM fx_fv f WHERE f.adsh = pivot_num.adsh AND f.ddate = pivot_num.ddate
                          AND f.identifier = pivot_num.identifier)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE core.holdings_unit_fixes AS
        SELECT f.cik, u.ddate AS period_end, u.adsh, u.identifier, u.legal_entity, u.fix
        FROM unit_fixes u JOIN core.filings f USING (adsh)
        """
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
    # c is a child of p when p is a word-boundary prefix of c ("Debt Investments Healthcare" ->
    # "Debt Investments Healthcare Acme Corp ...", "Debt Securities- United States" ->
    # "Debt Securities- United States Software").
    con.execute(
        """
        CREATE OR REPLACE TEMP MACRO is_child(c, p) AS
            c <> p AND length(c) > length(p) AND starts_with(lower(c), lower(p))
            AND substr(c, length(p) + 1, 1) IN (' ', '-', ',', '|', ':', ';', '(')
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP MACRO has_total_token(i) AS
            regexp_matches(i, '(?i)(^|[\\s,|(-])total($|[\\s,|)-])')
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP MACRO strip_total_token(i) AS
            trim(regexp_replace(regexp_replace(i, '(?i)(^|[\\s,|(-])total($|[\\s,|)-])', '\\1', 'g'),
                                '\\s{2,}', ' ', 'g'), ' ,|-')
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
                                         ORDER BY (cost IS NULL), identifier) AS rn
            FROM live
        )
        SELECT adsh, ddate, identifier, legal_entity, 'punct_dupe' FROM ranked WHERE rn > 1
        """
    )
    # R2c. A fair-value-only twin of a row that carries cost, same issuer and same fair value
    #      (Main Street, Capital Southwest tag "X | Secured Debt" with cost and "X | Secured Debt 1"
    #      with the fair value again): keep the row with cost.
    con.execute(
        """
        INSERT INTO excluded
        SELECT a.adsh, a.ddate, a.identifier, a.legal_entity, 'fv_only_twin'
        FROM pivot_num a JOIN parsed_idents pa ON pa.identifier = a.identifier
        JOIN pivot_num b ON b.adsh = a.adsh AND b.ddate = a.ddate AND b.legal_entity = a.legal_entity
             AND b.identifier <> a.identifier AND b.fair_value = a.fair_value AND b.cost IS NOT NULL
        JOIN parsed_idents pb ON pb.identifier = b.identifier
             AND (pb.issuer_norm = pa.issuer_norm
                  -- note rows glue footnote markers or "(dba X)" to the name: same first 12 characters
                  OR lower(substr(a.identifier, 1, 12)) = lower(substr(b.identifier, 1, 12)))
        WHERE a.cost IS NULL AND a.fair_value > 0 AND pa.issuer_norm <> ''
          AND is_live(a.adsh, a.ddate, a.identifier, a.legal_entity)
          AND is_live(b.adsh, b.ddate, b.identifier, b.legal_entity)
        """
    )
    # (R3, an unconditional drop of fair-value-only rows in filings that tag cost everywhere,
    #  removed OFS's equity positions; the reconciliation-gated 'no_cost' group in R9 covers it.)
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
    # R7b. "Total <issuer>" rows written beside the issuer's tranches, in filings where the
    #      tranches carry the same heading ("... Finance and Insurance Total Empower Financial"
    #      next to "... Finance and Insurance Empower Financial Type of Investment ..."). Unlike
    #      R7 the row may carry principal (the filer sums it too); the amounts must match the
    #      tranches' sums.
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.cost,
                   split_part(n.identifier, ' ', 1) AS w1
            FROM pivot_num n WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        tot AS (
            SELECT *, strip_total_token(identifier) AS key FROM live WHERE has_total_token(identifier)
        ),
        ch AS (
            SELECT t.adsh, t.ddate, t.legal_entity, t.identifier,
                   sum(c.fair_value) AS child_fv, sum(c.cost) AS child_cost, count(*) AS n_child,
                   count(c.cost) AS n_child_cost
            FROM tot t JOIN live c ON c.adsh = t.adsh AND c.ddate = t.ddate
                 AND c.legal_entity = t.legal_entity AND c.identifier <> t.identifier
                 AND (c.identifier = t.key OR is_child(c.identifier, t.key))
                 AND NOT has_total_token(c.identifier)
            WHERE length(t.key) >= 3
            GROUP BY 1, 2, 3, 4
        )
        SELECT t.adsh, t.ddate, t.identifier, t.legal_entity, 'issuer_total_row'
        FROM tot t JOIN ch ON ch.adsh = t.adsh AND ch.ddate = t.ddate
             AND ch.legal_entity = t.legal_entity AND ch.identifier = t.identifier
        WHERE t.fair_value IS NOT NULL AND ch.child_fv IS NOT NULL
          AND abs(t.fair_value - ch.child_fv) <= 0.01 * greatest(abs(t.fair_value), 1)
          AND (t.cost IS NULL OR ch.n_child_cost = 0
               OR abs(t.cost - ch.child_cost) <= 0.01 * greatest(abs(t.cost), 1))
        """
    )
    # R8. Heading rows in space-separated hierarchies ("Debt Investments" > "Debt Investments
    #     Healthcare" > "Debt Investments Healthcare Acme Corp First-lien ..."): a row that is a
    #     word-boundary prefix of other rows and whose amounts equal the sum of the leaves under
    #     it (rows in its subtree that are not themselves prefixes). Comparing against leaves
    #     makes nesting depth irrelevant. R6 only handles prefixes at explicit separators.
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.cost,
                   split_part(n.identifier, ' ', 1) AS w1,
                   (n.principal IS NULL AND n.rate IS NULL AND n.spread IS NULL
                    AND n.shares IS NULL AND n.pik_rate IS NULL) AS bare
            FROM pivot_num n WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        parents AS (
            SELECT DISTINCT p.adsh, p.ddate, p.legal_entity, p.identifier, p.w1
            FROM live p JOIN live c ON c.adsh = p.adsh AND c.ddate = p.ddate
                 AND c.legal_entity = p.legal_entity AND c.w1 = p.w1
                 AND is_child(c.identifier, p.identifier)
        ),
        leaves AS (
            SELECT l.* FROM live l ANTI JOIN parents USING (adsh, ddate, legal_entity, identifier)
        ),
        agg AS (
            SELECT p.adsh, p.ddate, p.legal_entity, p.identifier,
                   sum(c.fair_value) AS leaf_fv, sum(c.cost) AS leaf_cost,
                   count(*) AS n_leaf, count(c.cost) AS n_leaf_cost
            FROM parents p JOIN leaves c ON c.adsh = p.adsh AND c.ddate = p.ddate
                 AND c.legal_entity = p.legal_entity AND c.w1 = p.w1
                 AND is_child(c.identifier, p.identifier)
            GROUP BY 1, 2, 3, 4
        )
        SELECT p.adsh, p.ddate, p.identifier, p.legal_entity, 'heading_subtotal'
        FROM live p JOIN agg a USING (adsh, ddate, legal_entity, identifier)
        WHERE p.fair_value IS NOT NULL AND a.leaf_fv IS NOT NULL
          AND abs(p.fair_value - a.leaf_fv) <= 0.01 * greatest(abs(p.fair_value), 1)
          AND (p.cost IS NULL OR a.n_leaf_cost = 0
               OR abs(p.cost - a.leaf_cost) <= 0.01 * greatest(abs(p.cost), 1))
          AND (a.n_leaf >= 2 OR p.bare
               -- one child with the same amounts: the same holding tagged again with words
               -- appended ("... First Lien Loan" / "... First Lien Loan - Casual Dining")
               OR (a.n_leaf = 1 AND p.cost IS NOT NULL AND a.n_leaf_cost = 1))
        """
    )
    # R8b. A row without digits and without instrument facts that is a word-boundary prefix of
    #      two or more other rows is a heading ("Control Investments"), whatever its amount.
    con.execute(
        """
        INSERT INTO excluded
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, split_part(n.identifier, ' ', 1) AS w1,
                   (n.principal IS NULL AND n.rate IS NULL AND n.spread IS NULL
                    AND n.shares IS NULL AND n.pik_rate IS NULL) AS bare
            FROM pivot_num n WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        )
        SELECT p.adsh, p.ddate, p.identifier, p.legal_entity, 'heading_bare'
        FROM live p JOIN live c ON c.adsh = p.adsh AND c.ddate = p.ddate AND c.legal_entity = p.legal_entity
             AND c.w1 = p.w1 AND is_child(c.identifier, p.identifier)
        WHERE p.bare AND NOT regexp_matches(p.identifier, '[0-9]') AND length(p.identifier) < 80
        GROUP BY 1, 2, 3, 4 HAVING count(*) >= 2
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
    con.register(
        "industry_vocab",
        pl.DataFrame({"industry_lc": sorted({i.lower().strip() for i in extra_industries})},
                     schema={"industry_lc": pl.Utf8}),
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE r9_live AS
        WITH live AS (
            SELECT n.adsh, n.ddate, n.identifier, n.legal_entity, n.fair_value, n.principal, n.shares,
                   n.cost, n.rate, n.spread, n.pik_rate, n.sig AS words3, p.issuer_norm,
                   n.src, n.axis_set,
                   -- note tables listing portfolio companies per industry ("83bar, Akoya, Analogic, ...")
                   (p.instrument_type = 'unknown'
                    AND length(n.identifier) - length(replace(n.identifier, ',', '')) >= 4) AS is_name_list,
                   -- same issuer, same fair value and cost under another wording: a filer that
                   -- tags its schedule twice (Rand) or equal-sized tranches (Horizon); decided by R9
                   row_number() OVER (PARTITION BY n.adsh, n.ddate, n.legal_entity, p.issuer_norm,
                                      n.fair_value, coalesce(n.cost, -1)
                                      ORDER BY (n.principal IS NOT NULL)::INT + (n.rate IS NOT NULL)::INT
                                               + (n.spread IS NOT NULL)::INT + (n.shares IS NOT NULL)::INT DESC,
                                               length(n.identifier) DESC, n.identifier) AS amt_rn,
                   fa.footnote_text IS NOT NULL AS has_footnote,
                   position('%' IN n.identifier) > 0 AS has_pct,
                   regexp_matches(n.identifier, '(?i)(money market|treasury bill|t-bill|cash equivalent|'
                                                'u\\.?s\\.? treasury|government (money market|fund|portfolio)|'
                                                'liquidity fund|institutional (cash|liquid))') AS is_cash_equiv,
                   position('|' IN n.identifier) > 0 AS piped,
                   trim(split_part(n.identifier, '|', 1)) AS seg1,
                   split_part(n.identifier, ' ', 1) AS w1,
                   (n.principal IS NULL AND n.rate IS NULL AND n.spread IS NULL
                    AND n.shares IS NULL AND n.pik_rate IS NULL) AS bare
            FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
            LEFT JOIN footnote_agg fa ON fa.adsh = n.adsh AND fa.ddate = n.ddate
                 AND fa.identifier = n.identifier AND fa.legal_entity = n.legal_entity
            WHERE is_live(n.adsh, n.ddate, n.identifier, n.legal_entity)
        ),
        parents AS (
            SELECT p.adsh, p.ddate, p.legal_entity, p.identifier, count(*) AS n_child
            FROM live p JOIN live c ON c.adsh = p.adsh AND c.ddate = p.ddate
                 AND c.legal_entity = p.legal_entity AND c.w1 = p.w1
                 AND is_child(c.identifier, p.identifier)
            GROUP BY 1, 2, 3, 4
        )
        SELECT l.*, t.total_fv, t.assets,
               coalesce(pa.n_child, 0) >= 2 AS is_heading,
               coalesce(pa.n_child, 0) = 1 AS is_heading1,
               has_total_token(l.identifier) AS is_total_token,
               lower(trim(l.identifier)) IN (SELECT industry_lc FROM industry_vocab) AS is_industry_name,
               (l.bare AND t.total_fv > 0
                AND abs(l.fair_value - t.total_fv) <= 0.02 * t.total_fv) AS is_filing_total,
               -- "Investments242.6% of Net Assets", "... First Lien Secured Debt113.2%"
               (l.bare AND regexp_matches(l.identifier, '\\d(\\.\\d+)?\\s?%\\s*$')) AS is_pct_heading,
               -- category rows: no digits at all and no instrument-level facts other than
               -- principal ("First Lien - Secured Debt", "Household Durables First and Second Lien Debt")
               (NOT regexp_matches(l.identifier, '[0-9]') AND length(l.identifier) < 120
                AND l.rate IS NULL AND l.spread IS NULL AND l.shares IS NULL AND l.pik_rate IS NULL)
                   AS is_nodigit,
               -- rows with no instrument-level facts at all (industry / category headings
               -- placed mid-hierarchy: "Non-control investments - 256.5% - Healthcare Services")
               (l.bare AND length(l.identifier) < 120) AS is_bare
        FROM live l
        LEFT JOIN filing_totals t ON t.adsh = l.adsh AND t.ddate = l.ddate
        LEFT JOIN parents pa ON pa.adsh = l.adsh AND pa.ddate = l.ddate
             AND pa.legal_entity = l.legal_entity AND pa.identifier = l.identifier
        """
    )
    # One group per row, decided once, so that the cluster search and the drop agree exactly.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE r9_tagged AS
        WITH live AS (SELECT * FROM r9_live),
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
                   avg((cost IS NOT NULL)::INT) AS cost_share,
                   avg(has_footnote::INT) AS fn_share,
                   count(*) FILTER (WHERE amt_rn > 1) AS n_amt_dupe,
                   count(*) FILTER (WHERE has_pct) AS n_pct,
                   count(*) FILTER (WHERE NOT has_pct) AS n_nopct
            FROM live GROUP BY 1, 2
        ),

        seg AS (
            SELECT adsh, ddate, seg1, count(*) AS n_seg FROM live WHERE piped GROUP BY 1, 2, 3
        ),
        dominant AS (
            SELECT adsh, ddate, arg_max(seg1, n_seg) AS dom_seg, max(n_seg) AS dom_n FROM seg GROUP BY 1, 2
        )
        SELECT l.adsh, l.ddate, l.identifier, l.legal_entity, l.fair_value, l.total_fv,
               coalesce(
                   CASE WHEN l.is_filing_total THEN 'filing_total'
                        WHEN l.src = 'member_fill' THEN 'member_fill:' || l.axis_set
                        WHEN l.src = 'member' THEN 'axes:' || l.axis_set
                        WHEN l.is_cash_equiv THEN 'cash_equiv'
                        WHEN l.is_name_list THEN 'name_list'
                        WHEN l.cost IS NULL AND l.fair_value > 0 AND st.cost_share >= 0.8 THEN 'no_cost'
                        WHEN st.n_amt_dupe >= 5 AND st.n_amt_dupe >= 0.1 * st.n AND l.amt_rn > 1 THEN 'amount_dupe'
                        WHEN l.is_heading THEN 'heading'
                        WHEN l.is_heading1 THEN 'heading1'
                        WHEN l.is_total_token THEN 'total_token'
                        WHEN l.is_industry_name THEN 'industry_name'
                        WHEN l.is_pct_heading THEN 'pct_heading'
                        WHEN l.is_nodigit THEN 'nodigit'
                        WHEN l.is_bare THEN 'bare'
                        WHEN l.cost IS NULL AND l.fair_value > 0 AND st.cost_share >= 0.8 THEN 'no_cost'
                        WHEN st.piped_share >= 0.6 AND NOT l.piped THEN 'unpiped'
                        WHEN st.ps_share >= 0.75 AND l.principal IS NULL AND l.shares IS NULL THEN 'no_principal_or_shares'
                        WHEN st.fn_share >= 0.5 AND NOT l.has_footnote THEN 'no_footnote'
                        WHEN l.piped AND sg.n_seg >= 5 AND d.dom_n >= 20 AND l.seg1 <> d.dom_seg
                             AND sg.n_seg < d.dom_n THEN 'section:' || l.seg1
                        WHEN l.piped AND sg.n_seg >= 5 AND st.piped_share <= 0.4 THEN 'section:' || l.seg1
                        WHEN st.piped_share < 0.5 AND w.n_w >= 20 AND w.n_w < wd.dom_w
                             THEN 'words:' || l.words3
                        WHEN st.n_pct >= 10 AND st.n_nopct >= 10 AND st.piped_share < 0.5
                             AND l.has_pct <> (st.n_pct > st.n_nopct) THEN 'shape:' || CASE WHEN l.has_pct THEN 'pct' ELSE 'nopct' END
                        WHEN c1.n_tag >= 10 AND c1.n_tag <= 0.6 * st.n THEN 'custom:' || c1.ctag
                        ELSE NULL END, '__keep__') AS sig
        FROM live l
        JOIN stats st ON st.adsh = l.adsh AND st.ddate = l.ddate
        LEFT JOIN seg sg ON sg.adsh = l.adsh AND sg.ddate = l.ddate AND sg.seg1 = l.seg1
        LEFT JOIN dominant d ON d.adsh = l.adsh AND d.ddate = l.ddate
        LEFT JOIN w3 w ON w.adsh = l.adsh AND w.ddate = l.ddate AND w.words3 = l.words3
        LEFT JOIN w3dom wd ON wd.adsh = l.adsh AND wd.ddate = l.ddate
        LEFT JOIN ct1 c1 ON c1.adsh = l.adsh AND c1.ddate = l.ddate AND c1.identifier = l.identifier
        WHERE st.n >= 5 AND l.total_fv > 0
        """
    )
    groups = con.execute(
        """
        SELECT adsh, ddate, sig, sum(fair_value) AS fv, count(*) AS n, any_value(total_fv) AS total_fv
        FROM r9_tagged GROUP BY 1, 2, 3
        """
    ).pl()
    drops = _choose_cluster_drops(groups)
    if not drops.is_empty():
        drops = drops.filter(pl.col("sig") != "__keep__")
    con.register("cluster_drops", drops)
    con.register("r9_groups", groups)
    # kept for debugging: every candidate group per filing and whether it was dropped
    con.execute(
        """
        CREATE OR REPLACE TABLE core.holdings_clusters AS
        SELECT f.cik, g.ddate AS period_end, g.adsh, g.sig, g.fv, g.n, g.total_fv,
               d.sig IS NOT NULL AS dropped
        FROM r9_groups g JOIN core.filings f USING (adsh)
        LEFT JOIN cluster_drops d ON d.adsh = g.adsh AND d.ddate = g.ddate AND d.sig = g.sig
        """
    )
    con.execute(
        """
        INSERT INTO excluded
        SELECT l.adsh, l.ddate, l.identifier, l.legal_entity,
               CASE WHEN c.sig IN ('filing_total', 'heading', 'heading1', 'total_token', 'industry_name',
                                   'pct_heading', 'nodigit', 'bare', 'cash_equiv', 'no_footnote', 'no_cost',
                                   'amount_dupe', 'name_list')
                    THEN c.sig
                    WHEN c.sig LIKE 'axes:%' OR c.sig LIKE 'member_fill:%' THEN 'member_category'
                    ELSE 'note_schedule' END
        FROM r9_tagged l JOIN cluster_drops c ON c.adsh = l.adsh AND c.ddate = l.ddate AND c.sig = l.sig
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
                   sum(fair_value) AS sum_fv,
                   avg((src = 'member')::INT) AS member_share
            FROM pivot_num n
            WHERE NOT EXISTS (SELECT 1 FROM excluded e WHERE e.adsh = n.adsh AND e.ddate = n.ddate
                              AND e.identifier = n.identifier AND e.legal_entity = n.legal_entity)
            GROUP BY adsh, ddate
        ),
        own AS (
            SELECT c.adsh, c.n_holdings AS own_n FROM counts c JOIN core.filings f USING (adsh)
            WHERE f.period = c.ddate
        ),
        bdc_n AS (
            SELECT f.cik, max(o.own_n) AS bdc_max_n FROM own o JOIN core.filings f USING (adsh) GROUP BY 1
        ),
        ranked AS (
            SELECT f.cik, c.ddate AS period_end, c.adsh, f.form, f.filed, f.period,
                   c.n_holdings, c.sum_fv,
                   t.total_fv > 0 AND abs(c.sum_fv / t.total_fv - 1) <= 0.05 AS reconciles,
                   row_number() OVER (
                       PARTITION BY f.cik, c.ddate
                       ORDER BY (t.total_fv > 0 AND abs(c.sum_fv / t.total_fv - 1) <= 0.05) DESC,
                                (c.member_share < 0.5) DESC,
                                (f.period = c.ddate) DESC, f.filed DESC, c.n_holdings DESC
                   ) AS rn
            FROM counts c JOIN core.filings f USING (adsh)
            LEFT JOIN filing_totals t ON t.adsh = c.adsh AND t.ddate = c.ddate
            LEFT JOIN own o ON o.adsh = c.adsh
            LEFT JOIN bdc_n b ON b.cik = f.cik
            WHERE c.ddate <= current_date  -- a few filers tag a wrong (future) period date
              -- prior-period dates inside a filing that carry only a handful of rows compared
              -- with the BDC's schedules are affiliate roll-forward tables, not schedules
              AND ((f.period = c.ddate AND (t.total_fv > 0 AND abs(c.sum_fv / t.total_fv - 1) <= 0.1
                                            OR c.n_holdings >= 0.25 * coalesce(b.bdc_max_n, 0))
                                       AND NOT (c.member_share >= 0.5 AND t.total_fv > 0 AND c.sum_fv < 0.5 * t.total_fv))
                   OR (f.period <> c.ddate
                       AND (coalesce(greatest(o.own_n, b.bdc_max_n), o.own_n, b.bdc_max_n) IS NULL
                            OR c.n_holdings >= 0.25 * coalesce(greatest(o.own_n, b.bdc_max_n), o.own_n, b.bdc_max_n))))
        )
        SELECT * FROM ranked WHERE rn = 1
        """
    )

    # Principal written in the identifier ("($80,704 par, due 7/2028)") for filers that do not tag
    # it (Sixth Street). The scale (dollars or thousands) is chosen per filing-period: the one that
    # puts the median fair value / par between 0.5 and 1.5.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE par_scale AS
        WITH c AS (
            SELECT n.adsh, n.ddate, n.fair_value / p.par_amount AS r1, n.fair_value / (p.par_amount * 1000) AS r1000
            FROM pivot_num n JOIN parsed_idents p ON p.identifier = n.identifier
            WHERE p.par_amount > 0 AND n.fair_value > 0 AND n.principal IS NULL
        ),
        m AS (SELECT adsh, ddate, count(*) AS n, median(r1) AS m1, median(r1000) AS m1000 FROM c GROUP BY 1, 2)
        SELECT adsh, ddate,
               CASE WHEN m1000 BETWEEN 0.5 AND 1.5 THEN 1000.0 WHEN m1 BETWEEN 0.5 AND 1.5 THEN 1.0 END AS scale
        FROM m WHERE n >= 10
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
                   WHEN p.instrument_type IN ('equity','equity_other','unknown') AND n.principal > 0
                        AND (n.rate IS NOT NULL OR n.spread IS NOT NULL) THEN TRUE
                   WHEN p.instrument_type IN ('equity','preferred','warrant') THEN FALSE
                   WHEN mt.m_is_debt IS NOT NULL THEN mt.m_is_debt
                   WHEN me.m_is_debt IS NOT NULL THEN me.m_is_debt
                   WHEN n.principal IS NOT NULL OR n.rate IS NOT NULL OR n.spread IS NOT NULL
                        OR n.floor_rate IS NOT NULL OR n.pik_rate IS NOT NULL THEN TRUE
                   WHEN n.shares IS NOT NULL THEN FALSE
                   ELSE p.is_debt_text
               END AS is_debt,
               n.fair_value, n.cost,
               coalesce(n.principal, p.par_amount * sc.scale) AS principal, n.shares,
               coalesce(nullif(fix_rate(n.rate), 0),
                        fix_rate(n.cash_rate) + fix_rate(n.pik_rate),
                        nullif(fix_rate(n.cash_rate), 0), nullif(fix_rate(n.pik_rate), 0)) AS rate,
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
               CASE WHEN coalesce(n.principal, p.par_amount * sc.scale) <> 0
                    THEN n.fair_value / coalesce(n.principal, p.par_amount * sc.scale) END AS fv_to_principal
        FROM pivot_num n
        JOIN core.period_source ps ON ps.adsh = n.adsh AND ps.period_end = n.ddate
        LEFT JOIN par_scale sc ON sc.adsh = n.adsh AND sc.ddate = n.ddate
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
        ),
        -- when the detail misses the preferred total but matches another reported total
        -- within 2% (a filer whose schedule includes cash equivalents while the balance-sheet
        -- line excludes them, or vice versa), reconcile against that one
        alt AS (
            SELECT d.adsh, d.period_end,
                   arg_min(c.value, abs(d.detail_fv / c.value - 1)) AS alt_fv,
                   arg_min(c.source, abs(d.detail_fv / c.value - 1)) AS alt_source
            FROM det d JOIN total_candidates c ON c.adsh = d.adsh AND c.ddate = d.period_end
            WHERE c.in_band AND abs(d.detail_fv / c.value - 1) <= 0.02
            GROUP BY 1, 2
        ),
        pick AS (
            SELECT d.adsh, d.period_end,
                   CASE WHEN t.total_fv > 0 AND abs(d.detail_fv / t.total_fv - 1) <= 0.05 OR a.alt_fv IS NULL
                        THEN t.total_fv ELSE a.alt_fv END AS total_fv,
                   CASE WHEN t.total_fv > 0 AND abs(d.detail_fv / t.total_fv - 1) <= 0.05 OR a.alt_fv IS NULL
                        THEN t.total_source ELSE a.alt_source END AS total_source
            FROM det d LEFT JOIN tot t ON t.adsh = d.adsh AND t.ddate = d.period_end
            LEFT JOIN alt a ON a.adsh = d.adsh AND a.period_end = d.period_end
        )
        SELECT d.cik, d.period_end, d.adsh, d.n_holdings, d.n_debt, d.n_nonaccrual,
               d.detail_fv, p.total_fv, t.assets,
               CASE WHEN p.total_fv > 0 THEN d.detail_fv / p.total_fv END AS coverage,
               CASE WHEN t.assets > 0 THEN d.detail_fv / t.assets END AS detail_to_assets,
               coalesce(o.note,
                        CASE WHEN p.total_fv IS NULL AND t.assets > 0
                                  AND d.detail_fv / t.assets BETWEEN 0.85 AND 1.05
                             THEN 'no comparable reported total; detail is 85-105% of total assets' END)
                   AS override_note,
               p.total_source
        FROM det d LEFT JOIN tot t ON t.adsh = d.adsh AND t.ddate = d.period_end
        LEFT JOIN pick p ON p.adsh = d.adsh AND p.period_end = d.period_end
        LEFT JOIN ref.reconciliation_overrides o ON o.cik = d.cik
        """
    )
    for t in ("facts_num", "facts_txt", "pivot_num", "pivot_txt", "footnotes", "footnote_agg",
              "excluded", "pivot_all", "filing_totals", "total_candidates", "unit_suspects", "unit_fixes",
              "r9_live", "r9_tagged", "member_ctx", "member_leaf", "member_ident", "facts_member",
              "custom_tag_rows", "fx_fv", "par_scale", "ix_override"):
        con.execute(f"DROP TABLE IF EXISTS {t}")

    n, nf, np_ = con.execute(
        "SELECT count(*), count(DISTINCT cik), count(DISTINCT (cik, period_end)) FROM core.holdings"
    ).fetchone()
    return f"core.holdings: {n:,} rows, {nf} BDCs, {np_} BDC-periods"
