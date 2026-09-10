"""BDC-level fundamentals from the filing's own income statement and balance sheet
(signals.bdc_fundamentals): net investment income, dividend coverage, leverage, NAV per share
trend, realised losses. These are the stock-level metrics every BDC analyst starts with; the
loan-level tables say what the book looks like, these say what it earns and how it is funded.

Quarterly income-statement facts carry qtrs = 1 (three months) in both 10-Qs and 10-Ks; where a
filer only tags the year (qtrs = 4) the fourth quarter is the year less the nine-month figure
(qtrs = 3) at the previous quarter end.
"""
from __future__ import annotations

import duckdb

# tag -> column; several filers use the alternative names
FLOW_TAGS = {
    "NetInvestmentIncome": "nii",
    "InvestmentIncomeNet": "nii_alt",
    "InvestmentIncomeOperatingAfterExpenseAndTax": "nii_alt2",
    "GrossInvestmentIncomeOperating": "gross_income",
    "InvestmentIncomeInterest": "interest_income",
    "InterestExpense": "interest_expense",
    "RealizedInvestmentGainsLosses": "realized_gl",
    "NetRealizedGainLossOnInvestments": "realized_gl_alt",
    "WeightedAverageNumberOfSharesOutstandingBasic": "wavg_shares",
    "InterestIncomePaidInKind": "pik_income",
}
STOCK_TAGS = {
    "StockholdersEquity": "net_assets",
    "NetAssets": "net_assets_alt",
    "LongTermDebt": "debt",
    "DebtInstrumentCarryingAmount": "debt_alt",
    "DebtLongtermAndShorttermCombinedAmount": "debt_alt2",
    "LineOfCredit": "loc",
    "NotesPayable": "notes",
    "DebtInstrumentFaceAmount": "debt_face",
    "CommonStockSharesOutstanding": "shares",
    "SharesOutstanding": "shares_alt",
}


def build_fundamentals(con: duckdb.DuckDBPyConnection) -> str:
    flow_cols = ",\n".join(f"max(v) FILTER (WHERE tag = '{t}') AS {c}" for t, c in FLOW_TAGS.items())
    stock_cols = ",\n".join(f"max(v) FILTER (WHERE tag = '{t}') AS {c}" for t, c in STOCK_TAGS.items())
    all_tags = ", ".join(f"'{t}'" for t in list(FLOW_TAGS) + list(STOCK_TAGS))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE signals.bdc_fundamentals AS
        WITH facts AS (
            -- one value per (cik, date, qtrs, tag): the most recently filed fact wins (restatements)
            SELECT f.cik, n.ddate, n.qtrs, n.tag, arg_max(n.value, f.filed) AS v
            FROM raw.num n JOIN core.filings f USING (adsh)
            WHERE n.tag IN ({all_tags}) AND (n.segments IS NULL OR n.segments = '')
              AND (n.uom = 'USD' OR n.uom = 'shares' OR n.uom = 'pure')
              AND n.value IS NOT NULL
            GROUP BY 1, 2, 3, 4
        ),
        periods AS (SELECT DISTINCT cik, period_end FROM core.period_source),
        q1 AS (SELECT cik, ddate, {flow_cols} FROM facts WHERE qtrs = 1 GROUP BY 1, 2),
        q4 AS (SELECT cik, ddate, {flow_cols} FROM facts WHERE qtrs = 4 GROUP BY 1, 2),
        q3 AS (SELECT cik, ddate, {flow_cols} FROM facts WHERE qtrs = 3 GROUP BY 1, 2),
        st AS (SELECT cik, ddate, {stock_cols} FROM facts WHERE qtrs = 0 GROUP BY 1, 2),
        -- fourth quarter as year less nine months when only the year is tagged
        flow AS (
            SELECT p.cik, p.period_end,
                   {", ".join(f"coalesce(q1.{c}, q4.{c} - q3.{c}) AS {c}" for c in FLOW_TAGS.values())}
            FROM periods p
            LEFT JOIN q1 ON q1.cik = p.cik AND q1.ddate = p.period_end
            LEFT JOIN q4 ON q4.cik = p.cik AND q4.ddate = p.period_end
            LEFT JOIN q3 ON q3.cik = p.cik AND q3.ddate BETWEEN p.period_end - INTERVAL 100 DAY AND p.period_end - INTERVAL 80 DAY
        ),
        divs AS (
            -- quarterly dividend rate = trailing twelve months of cash dividends / 4 (Yahoo
            -- Finance ex-dates; a three-month window double counts when ex-dates sit at both
            -- quarter edges and misses specials unevenly)
            SELECT m.cik, p.period_end, sum(pr.dividend) / 4 AS div_ps
            FROM periods p JOIN ref.bdc_master m USING (cik)
            JOIN market.prices pr ON pr.ticker = m.ticker
                 AND pr.date > p.period_end - INTERVAL 365 DAY AND pr.date <= p.period_end
            WHERE pr.dividend > 0 GROUP BY 1, 2
            HAVING count(*) >= 2
        ),
        base AS (
            SELECT p.cik, p.period_end,
                   coalesce(fl.nii, fl.nii_alt, fl.nii_alt2) AS nii,
                   fl.gross_income, fl.interest_income, fl.interest_expense,
                   coalesce(fl.realized_gl, fl.realized_gl_alt) AS realized_gl,
                   fl.pik_income, fl.wavg_shares,
                   coalesce(st.net_assets, st.net_assets_alt) AS net_assets,
                   coalesce(st.debt, st.debt_alt, st.debt_alt2,
                            CASE WHEN st.loc IS NOT NULL OR st.notes IS NOT NULL
                                 THEN coalesce(st.loc, 0) + coalesce(st.notes, 0) END,
                            st.debt_face) AS debt,
                   -- tagged share counts are sometimes in thousands or the wrong class (Prospect):
                   -- the count implied by net assets / NAV per share is the reference
                   CASE WHEN nav.nav_per_share > 0 AND coalesce(st.net_assets, st.net_assets_alt) > 0
                              AND (coalesce(st.shares, st.shares_alt) IS NULL
                                   OR abs(coalesce(st.shares, st.shares_alt) * nav.nav_per_share
                                          / coalesce(st.net_assets, st.net_assets_alt) - 1) > 0.25)
                         THEN coalesce(st.net_assets, st.net_assets_alt) / nav.nav_per_share
                         ELSE coalesce(st.shares, st.shares_alt) END AS shares,
                   nav.nav_per_share, d.div_ps
            FROM periods p
            LEFT JOIN flow fl USING (cik, period_end)
            LEFT JOIN st ON st.cik = p.cik AND st.ddate = p.period_end
            LEFT JOIN market.nav nav USING (cik, period_end)
            LEFT JOIN divs d USING (cik, period_end)
        ),
        derived AS (
            SELECT b.*,
                   nii / nullif(CASE WHEN wavg_shares IS NOT NULL AND shares IS NOT NULL
                                          AND abs(wavg_shares / shares - 1) <= 0.25 THEN wavg_shares
                                     ELSE shares END, 0) AS nii_ps,
                   nii / nullif(CASE WHEN wavg_shares IS NOT NULL AND shares IS NOT NULL
                                          AND abs(wavg_shares / shares - 1) <= 0.25 THEN wavg_shares
                                     ELSE shares END, 0) / nullif(div_ps, 0) AS div_coverage,
                   4 * nii / nullif(net_assets, 0) AS nii_roe,
                   debt / nullif(net_assets, 0) AS leverage,
                   4 * interest_expense / nullif(debt, 0) AS cost_of_debt,
                   4 * gross_income / nullif(net_assets + coalesce(debt, 0), 0) AS gross_yield_on_capital,
                   realized_gl / nullif(net_assets, 0) AS realized_gl_rate,
                   pik_income / nullif(gross_income, 0) AS pik_income_share,
                   nav_per_share / nullif(q1.nav_1q, 0) - 1 AS nav_chg_1q,
                   nav_per_share / nullif(q4.nav_4q, 0) - 1 AS nav_chg_4q,
                   shares / nullif(q4.shares_4q, 0) - 1 AS share_chg_4q,
                   nii / nullif(q4.nii_4q, 0) - 1 AS nii_chg_4q
            FROM base b
            -- the comparison quarter is found by date, not by row: a quarter missing from the
            -- data (failed reconciliation, late filing) must not turn "one year ago" into five
            -- quarters ago
            LEFT JOIN LATERAL (
                SELECT x.nav_per_share AS nav_1q FROM base x WHERE x.cik = b.cik
                  AND x.period_end BETWEEN b.period_end - INTERVAL 105 DAY AND b.period_end - INTERVAL 75 DAY
                ORDER BY x.period_end DESC LIMIT 1
            ) q1 ON TRUE
            LEFT JOIN LATERAL (
                SELECT x.nav_per_share AS nav_4q, x.shares AS shares_4q, x.nii AS nii_4q FROM base x WHERE x.cik = b.cik
                  AND x.period_end BETWEEN b.period_end - INTERVAL 385 DAY AND b.period_end - INTERVAL 345 DAY
                ORDER BY x.period_end DESC LIMIT 1
            ) q4 ON TRUE
        )
        SELECT * FROM derived
        """
    )
    n, pub = con.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE m.is_public AND f.nii IS NOT NULL AND f.net_assets IS NOT NULL)
        FROM signals.bdc_fundamentals f JOIN ref.bdc_master m USING (cik)
        """
    ).fetchone()
    return f"signals.bdc_fundamentals: {n:,} BDC-periods, {pub:,} public with NII and net assets"
