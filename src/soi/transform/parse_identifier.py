"""Parse the free-text InvestmentIdentifierAxis member into issuer / instrument fields.

Formats differ per BDC. Examples seen in the data:
  "15484880 Canada Inc. | First lien senior secured loan 1"                          (ARCC)
  "Debt Investments, Application Software, Alchemer LLC, Senior Secured, May 2028,
   1-month SOFR + 8.14%, Floor rate 9.14%"                                           (HTGC)
  "Control investments - 10.9% - Pepper Palace, Inc. - Specialty Food Retailer -
   First Lien Term Loan 4.42% PIK, 1"                                                (SAR)
  "Investments United States Debt Investments Software & Services Benesys Inc.
   Investment Type Senior Secured ..."                                               (CCAP, breadcrumb)
  "Investment Debt Investments - 233.2% United States - 220.5% 1st Lien/Senior Secured
   Debt - 206.5% VRC Companies, LLC ..."                                             (GSBD, breadcrumb)
  "Mood Media Borrower, LLC, Debt investments, First Lien Term Loan|ISS=Mood_Media_Borrower_LLC
   TYP=Debtinvestments STY=First_Lien_Term_Loan GEO=US"                              (DQC style)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# ---- instrument classification --------------------------------------------------------------

# (instrument_type, is_debt, patterns) checked in order; first match wins.
INSTRUMENT_RULES: list[tuple[str, bool, list[str]]] = [
    ("warrant", False, [r"\bwarrants?\b"]),
    ("preferred", False, [r"\bpreferred\b", r"\bpref\.?\b", r"\bseries [a-z]\b.*\b(units?|shares?|stock)\b"]),
    ("equity", False, [
        r"\bcommon\b", r"\bequity\b", r"\bmembership\b",
        r"\b(member|members|partnership|llc|lp|limited partner|ownership|profits?|equity) interests?\b",
        r"\bunits?\b", r"\bshares?\b",
        r"\bstock\b", r"\bclass [a-z]\b", r"\bl\.?p\.? interest", r"\bpartnership interest",
        r"\bllc interest", r"\bordinary\b", r"\bprofits? interest", r"\bco-?invest",
    ]),
    ("second_lien", True, [r"\bsecond[- ]lien\b", r"\b2nd[- ]lien\b", r"\bsecond_lien\b", r"\bSLD\b", r"\bSLSD\b"]),
    ("subordinated", True, [
        r"\bsubordinat", r"\bmezz", r"\bjunior\b", r"\bunsecured\b", r"\bsenior notes?\b",
        r"\bbond", r"\bpik note", r"\bconvertible note",
    ]),
    ("first_lien", True, [
        r"\bfirst[- ]lien\b", r"\b1st[- ]lien\b", r"\bfirst_lien\b", r"\bunitranche\b", r"\bFLSD\b",
        r"\bone[- ]stop\b", r"\bsecured debt\b", r"\bgrowth capital\b", r"\bsenior secured\b",
        r"\bFLD\b",
        r"\bsenior debt\b", r"\bsenior loan\b", r"\bsenior term\b",
        r"\bterm loan\b", r"\brevolv", r"\bdelayed[- ]draw\b", r"\bddtl\b", r"\bloan\b",
        r"\bcredit facility\b", r"\bline of credit\b", r"\btl[ab]?\b", r"\bnotes?\b",
        r"\bdebt\b", r"\bsecured\b",
    ]),
    ("structured", True, [
        r"\bclo\b", r"\bcollateralized\b", r"\bstructured\b", r"\basset[- ]backed\b",
        r"\bjoint venture\b", r"\bjv\b", r"\bsdlp\b", r"\bslf\b", r"\bfund\b",
    ]),
]
_COMPILED_RULES = [(t, d, [re.compile(p, re.IGNORECASE) for p in ps]) for t, d, ps in INSTRUMENT_RULES]

SUBTYPE_RULES: list[tuple[str, str]] = [
    ("revolver", r"\brevolv"),
    ("delayed_draw", r"\bdelayed[- ]draw\b|\bddtl\b"),
    ("unitranche", r"\bunitranche\b|\bone[- ]stop\b"),
    ("last_out", r"\blast[- ]out\b"),
    ("first_out", r"\bfirst[- ]out\b"),
]
_COMPILED_SUBTYPES = [(n, re.compile(p, re.IGNORECASE)) for n, p in SUBTYPE_RULES]

# ---- vocab -----------------------------------------------------------------------------------

SUFFIX_WORDS = (
    r"llc|l\.l\.c\.|inc\.?|incorporated|corp\.?|corporation|co\.?|company|ltd\.?|limited|"
    r"l\.?p\.?|lp|llp|plc|gmbh|s\.?a\.?|s\.?a\.?s\.?|b\.?v\.?|n\.?v\.?|a/s|ab|ag|pty|holdings?|"
    r"group|partners|trust|s\.?r\.?l\.?|oy|sarl|s\.?à\.?r\.?l\.?|ltda|ulc|lc|pllc|pc|kg|as|"
    r"limited partnership|s\.?p\.?a\.?|aps|bidco|midco|topco|holdco|opco|borrower|buyer|"
    r"acquisition|acquisitions|parent|intermediate"
)
CORP_SUFFIX_RE = re.compile(rf"\b({SUFFIX_WORDS})(?![a-z])\.?", re.IGNORECASE)
# strong suffixes (legal-form words) used to anchor the end of a company name inside breadcrumbs
STRONG_SUFFIX_RE = re.compile(
    r"\b(llc|l\.l\.c\.|inc\.?|incorporated|corp\.?|corporation|ltd\.?|limited|l\.?p\.?|lp|llp|"
    r"plc|gmbh|s\.?a\.?|s\.?a\.?s\.?|b\.?v\.?|n\.?v\.?|a/s|ag|pty|s\.?r\.?l\.?|sarl|ltda|ulc|"
    r"pllc|kg|s\.?p\.?a\.?|aps|co\.|company)(?![a-z])\.?",
    re.IGNORECASE,
)

# Category / heading phrases that filers prepend to identifiers. Stripped from the start
# of a token repeatedly.
CATEGORY_PHRASES = [
    r"investments?( in)?", r"portfolio( companies| investments?)?", r"debt( investments?| securities)?",
    r"equity( investments?| securities| interests?)?", r"senior (secured )?(loans?|debt)",
    r"(first|second|1st|2nd)[- ]?lien(/senior secured)?( (debt|loans?|secured debt))?",
    r"secured (debt|loans?)", r"unsecured (debt|loans?)", r"subordinated (debt|loans?|notes?)",
    r"(non[- ]?)?(controlled?|control|affiliated?|affiliate)([/,&\s]+(non[- ]?)?(controlled?|control|affiliated?|affiliate))*"
    r"( (investments?|portfolio companies|issuers?|companies))?",
    r"controlled affiliate", r"non[- ]?affiliated?( issuers?)?", r"affiliated?( issuers?)?",
    r"issuer name", r"name of (issuer|company|portfolio company)", r"company name",
    r"non ?affiliate", r"life science", r"technology", r"healthcare", r"medical device", r"software",
    r"investment type", r"type", r"and", r"ncna", r"nca", r"inv\.", r"debt and equity inv\.?",
    r"debt and equity investments?", r"inv\.? in (ncna|nca|ca|c|na) prtfl comp", r"prtfl comp",
    r"flsd", r"slsd", r"sd", r"pe", r"ce",
    r"structured finance securities",
    r"portfolio company", r"issuer", r"company", r"investment",
    r"united states( of america)?", r"u\.?s\.?a?\b", r"canada", r"united kingdom", r"europe",
    r"australia", r"netherlands", r"germany", r"france", r"luxembourg", r"cayman islands",
    r"ireland", r"jersey", r"international", r"domestic", r"foreign",
    r"investment vehicles?", r"structured (credit|finance)( (investments?|securities))?",
    r"cash equivalents?", r"short[- ]term investments?", r"money market( funds?)?",
    r"other investments?", r"asset[- ]backed securities", r"private (credit|debt)",
    r"funds?", r"warrants?", r"preferred (stock|equity|units?)", r"common (stock|equity|units?)",
    r"loans?", r"notes?", r"total", r"sub[- ]?total",
    r"level [123]", r"n/a", r"-", r"–", r"—", r",", r":", r"\|",
    r"\d+(\.\d+)?\s?%",
    r"\(?\d+(\.\d+)?\s?%\)?( of net assets)?",
]
_CATEGORY_HEAD_RE = re.compile(
    r"^\s*(?:" + "|".join(f"(?:{p})" for p in CATEGORY_PHRASES) + r")(?=\s|$|[,;:|\-–—])[\s,:;|\-–—]*",
    re.IGNORECASE,
)

# Attribute clauses that appear after the instrument description; everything from the first one
# on is dropped before looking for the issuer name.
ATTRIBUTE_CLAUSE_RE = re.compile(
    r"\b(investment type|asset type|commitment type|interest rate|rate type|maturity( date)?|"
    r"acquisition date|spread|floor( rate)?|sofr|libor|prime|euribor|sonia|cdor|bbsw|"
    r"\d{1,2}-?month|par(?= |,|\))|due\b|pik\b|cash\b|coupon|yield|principal|shares?\b|units?\b|"
    r"\d+(\.\d+)?\s?%|\(\$[\d,]+)",
    re.IGNORECASE,
)

INDUSTRY_VOCAB_DEFAULT: tuple[str, ...] = (
    "Aerospace & Defense", "Air Freight & Logistics", "Airlines", "Auto Components",
    "Automobiles", "Automobiles & Components", "Automotive", "Banking", "Banks",
    "Beverage, Food & Tobacco", "Beverages", "Biotechnology", "Building Products",
    "Business Services", "Capital Equipment", "Capital Goods", "Capital Markets",
    "Chemicals", "Chemicals, Plastics & Rubber", "Commercial & Professional Services",
    "Commercial Services & Supplies", "Communications Equipment", "Construction & Building",
    "Construction & Engineering", "Consumer Discretionary", "Consumer Durables & Apparel",
    "Consumer Finance", "Consumer Goods: Durable", "Consumer Goods: Non-Durable",
    "Consumer Products", "Consumer Services", "Consumer Staples", "Containers & Packaging",
    "Containers, Packaging & Glass", "Distributors", "Diversified Consumer Services",
    "Diversified Financial Services", "Diversified Financials", "Diversified Telecommunication Services",
    "Education", "Electric Utilities", "Electrical Equipment", "Electronic Equipment, Instruments & Components",
    "Energy", "Energy Equipment & Services", "Energy: Electricity", "Energy: Oil & Gas",
    "Entertainment", "Environmental & Facilities Services", "Environmental Services",
    "Equity Real Estate Investment Trusts (REITs)", "FIRE: Finance", "FIRE: Insurance",
    "FIRE: Real Estate", "Financial Services", "Financials", "Food & Beverage",
    "Food & Staples Retailing", "Food Products", "Food, Beverage & Tobacco", "Forest Products & Paper",
    "Gaming, Lodging & Leisure", "Gas Utilities", "Health Care", "Health Care Equipment & Services",
    "Health Care Equipment & Supplies", "Health Care Providers & Services", "Health Care Technology",
    "Healthcare", "Healthcare & Pharmaceuticals", "Healthcare Services", "Healthcare Technology",
    "High Tech Industries", "Hotels, Restaurants & Leisure", "Household & Personal Products",
    "Household Durables", "Household Products", "IT Services", "Industrial Conglomerates",
    "Industrials", "Information Technology", "Insurance", "Insurance Services", "Interactive Media & Services",
    "Internet & Direct Marketing Retail", "Internet Software & Services", "Leisure Products",
    "Life Sciences Tools & Services", "Machinery", "Marine", "Media", "Media & Entertainment",
    "Media: Advertising, Printing & Publishing", "Media: Broadcasting & Subscription",
    "Media: Diversified & Production", "Metals & Mining", "Multiline Retail", "Oil, Gas & Consumable Fuels",
    "Oil & Gas", "Packaging", "Paper & Forest Products", "Paper & Packaging", "Personal Products",
    "Pharmaceuticals", "Pharmaceuticals, Biotechnology & Life Sciences", "Professional Services",
    "Real Estate", "Real Estate Management & Development", "Retail", "Retailing", "Road & Rail",
    "Semiconductors & Semiconductor Equipment", "Services: Business", "Services: Consumer",
    "Software", "Software & Services", "Sovereign & Public Finance", "Specialty Retail",
    "Technology Hardware & Equipment", "Technology Hardware, Storage & Peripherals",
    "Telecommunication Services", "Telecommunications", "Textiles, Apparel & Luxury Goods",
    "Trading Companies & Distributors", "Transportation", "Transportation: Cargo",
    "Transportation: Consumer", "Utilities", "Utilities: Electric", "Utilities: Oil & Gas",
    "Utilities: Water", "Water Utilities", "Wholesale", "Wireless Telecommunication Services",
    "Application Software", "Systems Software", "Data Processing & Outsourced Services",
    "Internet Services & Infrastructure", "Diversified Support Services", "Research & Consulting Services",
    "Human Resource & Employment Services", "Specialized Consumer Services", "Restaurants",
    "Leisure Facilities", "Education Services", "Health Care Services", "Health Care Facilities",
    "Managed Health Care", "Health Care Supplies", "Health Care Distributors", "Advertising",
    "Publishing", "Broadcasting", "Movies & Entertainment", "Interactive Home Entertainment",
    "Specialty Chemicals", "Industrial Machinery", "Building Products", "Electrical Components & Equipment",
    "Trucking", "Property & Casualty Insurance", "Insurance Brokers", "Asset Management & Custody Banks",
    "Specialty Finance", "Financial Services: Insurance", "Business Products & Services",
    "Consumer Products & Services", "Healthcare Products & Services", "Technology & Telecommunications",
)


def _industry_regex(vocab: tuple[str, ...]) -> re.Pattern[str]:
    parts = sorted({v.strip() for v in vocab if v and v.strip()}, key=len, reverse=True)
    return re.compile(
        r"^\s*(?:" + "|".join(re.escape(p) for p in parts) + r")(?=\s|$|[,;:|\-–—])[\s,:;|\-–—]*",
        re.IGNORECASE,
    )


_INDUSTRY_HEAD_RE = _industry_regex(INDUSTRY_VOCAB_DEFAULT)

_SPLIT_RE = re.compile(r"\s*\|\s*|\s+-\s+|\s+–\s+|\s+—\s+|\s*;\s*|\s*,\s*")
_SUFFIX_COMMA_RE = re.compile(
    rf",\s*(?=({SUFFIX_WORDS})(?![a-z])\.?(\s*[|,;\-–—]|\s*$|\s))",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"\b([A-Z]{2,4})=(\S+)")
_PCT_RE = re.compile(r"\(?-?\s*\d+(\.\d+)?\s?%\)?")
_SEQ_RE = re.compile(r"\s+\d{1,3}(\.\d{1,2})?$")

CATEGORY_TOKEN_RE = re.compile(
    r"^(debt|equity|non[- ]?control|control|affiliate|non[- ]?affiliate|affiliated|unaffiliated|"
    r"investments?|portfolio|senior|subordinated|structured|other|cash equivalents?|"
    r"money market|short[- ]term|united states|u\.?s\.?a?|canada|europe|united kingdom|"
    r"[\d.,]+%?|first lien|second lien|warrants?|preferred|common|total|sub ?total)"
    r"( (investments?|securities|debt|equity))?\.?$",
    re.IGNORECASE,
)
# A pipe segment made only of these words describes the instrument, never the issuer
# ("Senior Secured First Lien Term Loan | Advocates For Disabled Vets LLC").
_VOCAB_ONLY_RE = re.compile(
    r"^(?:(?:senior|secured|unsecured|first|second|third|1st|2nd|lien|term|loans?|debt|revolver|"
    r"revolving|delayed|draw|unitranche|notes?|preferred|common|stock|equity|equities|warrants?|units?|"
    r"interests?|subordinated|mezzanine|convertible|tranche|facility|credit|line|of|and|or|class|"
    r"series|membership|growth|capital|fixed|floating|rate|ddtl|tl[abc]?|investments?|securities|"
    r"other|cash|equivalents?|money|market|funds?|short|structured|clo|asset|backed|total|sub|"
    r"last|first|out|bridge|priority|super|junior|holdco|opco|pik|toggle|bond|bonds|"
    r"bank|life|science|sciences|healthcare|health|care|technology|software|based|abl|sponsor|"
    r"finance|financing|specialty|flow|recurring|revenue|venture|lending|program|equipment|leasing|"
    r"real|estate|mortgage|lender|lenders|at|percent|point|hundred|thousand|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|[a-z]|\d+(?:\.\d+)?%?)\b[\s,\-–—/()]*)+$",
    re.IGNORECASE,
)
RATE_DATE_RE = re.compile(
    r"(sofr|libor|prime|euribor|sonia|cdor|bbsw|floor|\bpik\b|\d+(\.\d+)?%|"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{4}\b|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}(-\d{2})?\b|\bdue\b|\bmatur)",
    re.IGNORECASE,
)

NORMALIZE_DROP = re.compile(
    r"\b(llc|inc|incorporated|corp|corporation|co|company|ltd|limited|lp|llp|plc|gmbh|sa|sas|"
    r"bv|nv|ab|ag|pty|holdings?|holdco|group|intermediate|parent|borrower|acquisition|"
    r"acquisitions|buyer|midco|topco|bidco|opco|merger sub|the|of|and|dba|d/b/a|fka|f/k/a|"
    r"nka|n/k/a|us|usa|international|intl|enterprises|industries|services|solutions)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
MATURITY_TEXT_RES = [
    re.compile(r"(?:maturity( date)?|due|matures?)[:\s]+(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(rf"(?:maturity( date)?|due|matures?)[:\s]+((?:{MONTHS})[a-z]*\.? \d{{1,2}},? \d{{4}}|(?:{MONTHS})[a-z]*\.? \d{{4}})", re.IGNORECASE),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(rf"\b((?:{MONTHS})[a-z]*\.? \d{{1,2}},? \d{{4}})\b", re.IGNORECASE),
    re.compile(rf"\b((?:{MONTHS})[a-z]*\.? \d{{4}})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2}/\d{4})\b"),
]


# ---- helpers ---------------------------------------------------------------------------------


def normalize_issuer(name: str) -> str:
    """Aggressive key for matching the same borrower across BDCs and quarters."""
    s = name.lower()
    s = re.sub(r"\(.*?\)", " ", s)  # drop parentheticals (dba X, fka Y)
    s = re.sub(r"\b([a-z])\.", r"\1", s)  # L.P. -> lp, U.S. -> us
    s = s.replace("&", " and ")
    s = _NON_ALNUM.sub(" ", s)
    s = NORMALIZE_DROP.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def classify_instrument(text: str) -> tuple[str, bool]:
    for itype, is_debt, pats in _COMPILED_RULES:
        if any(p.search(text) for p in pats):
            return itype, is_debt
    return "unknown", False


def classify_subtype(text: str) -> str | None:
    for name, pat in _COMPILED_SUBTYPES:
        if pat.search(text):
            return name
    return None


def strip_heads(text: str, industry_re: re.Pattern[str]) -> str:
    """Remove leading category phrases / industry names repeatedly."""
    prev = None
    s = text.strip()
    while s and s != prev:
        prev = s
        s = _CATEGORY_HEAD_RE.sub("", s, count=1)
        s = industry_re.sub("", s, count=1)
        s = s.strip()
    return s


def _cut_at_suffix(text: str) -> str | None:
    """'Beverage SauceCo HoldCo, LLC First Lien Loan' -> 'Beverage SauceCo HoldCo, LLC'"""
    m = STRONG_SUFFIX_RE.search(text)
    if not m:
        return None
    end = m.end()
    # "Alpha Inc. and Beta LLC" / "Alpha, Inc. / Beta Corp": extend across joined names
    while True:
        rest = text[end:]
        j = re.match(r"\s*(and|&|/)\s+", rest, re.IGNORECASE)
        if not j:
            break
        m2 = STRONG_SUFFIX_RE.search(rest, j.end())
        if not m2 or m2.start() - j.end() > 60:
            break
        end += m2.end()
    return text[:end].strip(" ,;|-")


def _clean_token(tok: str) -> str:
    return _WS.sub(" ", tok).strip(" |-,;").strip()


def _looks_like_company(tok: str) -> bool:
    if not tok or len(tok) < 2:
        return False
    if CATEGORY_TOKEN_RE.match(tok):
        return False
    if RATE_DATE_RE.search(tok) and not CORP_SUFFIX_RE.search(tok):
        return False
    itype, _ = classify_instrument(tok)
    if itype != "unknown" and not CORP_SUFFIX_RE.search(tok):
        return False
    return bool(re.search(r"[a-z]", tok, re.IGNORECASE))


def _strip_trailing_instrument(tok: str) -> str:
    """'ABB/Con-cise Optical Group LLC First Lien Secured Term Loan' -> up to the suffix."""
    cut = _cut_at_suffix(tok)
    if cut and len(cut) < len(tok):
        return cut
    # no legal suffix: cut before the first instrument keyword
    m = re.search(
        r"\b(first|second|1st|2nd|senior|subordinated|unitranche|one stop|term loan|revolver|"
        r"delayed draw|warrants?|preferred|common|equity|debt|loan|notes?|units?|shares?|"
        r"class [a-z]\b|investment type|asset type|interest rate|maturity)\b",
        tok, re.IGNORECASE,
    )
    if m and m.start() > 2:
        return tok[: m.start()].strip(" ,;|-")
    return tok


def extract_issuer(identifier: str, industry_re: re.Pattern[str] = _INDUSTRY_HEAD_RE) -> str:
    """Best-effort issuer/borrower name from the identifier string."""
    kv = dict(_KV_RE.findall(identifier))
    if "ISS" in kv:
        return _clean_token(kv["ISS"].replace("_", " "))
    head = identifier.split("|ISS=")[0] if "ISS=" in identifier else identifier
    head = re.split(r"\s+[A-Z]{2,4}=", head)[0]
    head = re.sub(r"\(.*?\)", " ", head)  # (fka X), (dba Y), (Pele Buyer, LLC)
    head = re.sub(r"[\^*†‡§]+", " ", head)  # footnote markers glued to names
    head = re.sub(rf"\b({SUFFIX_WORDS})\.?(\d{{1,3}})$", r"\1 \2", head, flags=re.IGNORECASE)  # "LLC2"
    head = re.sub(r"\.(?=[A-Z][a-z])", ". ", head)  # "Inc.Type of Investment"
    head = _PCT_RE.sub(" ", head)
    head = _SUFFIX_COMMA_RE.sub(" ", head)
    head = _WS.sub(" ", head).strip()
    # Pipe-delimited identifiers ("Issuer | instrument | affiliation"): the issuer is the first
    # segment that is not a category label. Never cut inside it (co-borrower names contain words
    # like Equity, Debt, Preferred, Fund).
    if "|" in head:
        for seg in head.split("|"):
            seg = _clean_token(_CATEGORY_HEAD_RE.sub("", seg.strip(), count=1))
            if not seg or CATEGORY_TOKEN_RE.match(seg) or _VOCAB_ONLY_RE.match(seg):
                continue
            if RATE_DATE_RE.search(seg) and not CORP_SUFFIX_RE.search(seg):
                continue
            return seg
    # strip category labels first, then drop attribute clauses (Interest Rate ..., Maturity
    # Date ..., SOFR + ...) that follow the issuer name
    head = strip_heads(head, industry_re)
    m = ATTRIBUTE_CLAUSE_RE.search(head)
    if m and m.start() > 0 and head[: m.start()].strip():
        head = head[: m.start()]
    head = strip_heads(head, industry_re)
    tokens = [_clean_token(t) for t in _SPLIT_RE.split(head)]
    tokens = [strip_heads(t, industry_re) for t in tokens]
    tokens = [t for t in tokens if t]
    if not tokens:
        return _clean_token(_PCT_RE.sub(" ", identifier))[:120]
    for t in tokens:
        if STRONG_SUFFIX_RE.search(t) and _looks_like_company(t):
            return _strip_trailing_instrument(t)
    for t in tokens:
        if _looks_like_company(t):
            return _strip_trailing_instrument(t)
    return _strip_trailing_instrument(tokens[0])


def head_stripped_words(identifier: str, industry_re: re.Pattern[str] = _INDUSTRY_HEAD_RE) -> list[str]:
    """Words of the identifier after the category heads, cut at the first corporate suffix
    ("... United States Space Technology Rocket Lab USA, Inc. Type of ..." ->
    ['Space', 'Technology', 'Rocket', 'Lab', 'USA,', 'Inc.']); used to learn industry phrases."""
    head = re.split(r"\s+[A-Z]{2,4}=", identifier)[0]
    head = _WS.sub(" ", _PCT_RE.sub(" ", head)).strip()
    stripped = strip_heads(head, industry_re)
    # only identifiers that start with category text ("Portfolio Company Debt Securities- United
    # States ...") can carry an untagged industry; a plain "New PLI Holdings, LLC" cannot
    if stripped == head:
        return []
    m = CORP_SUFFIX_RE.search(stripped)
    if not m:
        return []
    return stripped[: m.end()].split()


def instrument_text(identifier: str, issuer: str) -> str:
    """The part of the identifier that describes the instrument (everything except the issuer).
    For pipe-delimited identifiers this is every segment other than the issuer segment; otherwise
    the identifier with the issuer name removed."""
    if "|" in identifier:
        segs = [s.strip() for s in identifier.split("|")]
        rest = [s for s in segs if s and s != issuer.strip() and _WS.sub(" ", s) != _WS.sub(" ", issuer)]
        # the issuer may have been cleaned (", LLC" glue, parentheticals); drop the first segment
        # whenever it contains the issuer's first word
        if len(rest) == len(segs) and issuer:
            first_word = issuer.split()[0].lower() if issuer.split() else ""
            rest = [s for i, s in enumerate(segs) if not (i == 0 and first_word and first_word in s.lower())]
        return " | ".join(rest)
    body = _SUFFIX_COMMA_RE.sub(" ", identifier)
    if issuer:
        body = body.replace(issuer, " ")
        body = body.replace(_SUFFIX_COMMA_RE.sub(" ", issuer), " ")
    # category labels ("Debt and Equity Inv.", "Non-controlled ...") are not instrument words
    return strip_heads(_WS.sub(" ", body).strip(), _INDUSTRY_HEAD_RE)


# "($80,704 par, due 7/2028)", "(EUR 40,905 par" -> 80704.0 (scale decided per filing in SQL)
_PAR_RE = re.compile(r"\(\s*(?:[A-Z]{3}\s*)?\$?\s*([\d,]+(?:\.\d+)?)\s*par\b", re.IGNORECASE)


def extract_par_amount(identifier: str) -> float | None:
    m = _PAR_RE.search(identifier)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def extract_maturity_text(identifier: str) -> str | None:
    for pat in MATURITY_TEXT_RES:
        m = pat.search(identifier)
        if m:
            return m.group(m.lastindex or 1)
    return None


def ident_key(identifier: str) -> str:
    """Identifier with volatile pieces removed (percent-of-net-assets, whitespace runs) so the
    same holding matches across quarters even when the filer embeds moving numbers."""
    s = _PCT_RE.sub(" ", identifier)
    s = re.sub(r"\s+", " ", s).strip(" ,;|-")
    return s.lower()


# ---- InvestmentTypeAxis member fallback -----------------------------------------------------

MEMBER_RULES: list[tuple[str, bool, str]] = [
    ("warrant", False, r"warrant"),
    ("preferred", False, r"preferred"),
    ("equity", False, r"common|equity|ordinary|share|unit|membership|partnership|stock"),
    ("second_lien", True, r"secondlien"),
    ("subordinated", True, r"subordinat|mezz|unsecured|junior"),
    ("first_lien", True, r"firstlien|seniorsecured|seniorloan|growthcapital|revolver|termloan|loan|debt|note"),
    ("structured", True, r"clo|structured|jointventure|fund"),
]
_COMPILED_MEMBER_RULES = [(t, d, re.compile(p, re.IGNORECASE)) for t, d, p in MEMBER_RULES]


def classify_member(member: str | None) -> tuple[str, bool] | None:
    if not member:
        return None
    tail = member.rsplit("#", 1)[-1].rsplit(":", 1)[-1]
    for itype, is_debt, pat in _COMPILED_MEMBER_RULES:
        if pat.search(tail):
            return itype, is_debt
    return None


# ---- subtotal / aggregate rows tagged with the identifier axis ------------------------------

TOTAL_TOKEN_RE = re.compile(r"^\s*(sub[- ]?total|total|net assets|liabilities in excess)\b", re.IGNORECASE)
AGGREGATE_WHOLE_RE = re.compile(
    r"^\s*(other )?(investments?( before| after| net of)? cash equivalents?|"
    r"investments? in securities and cash equivalents?|cash( and cash)? equivalents?|"
    r"money market( funds?)?|short[- ]term investments?|largest portfolio company investments?|"
    r"top (five|ten|5|10) largest portfolio company investments?|investments?|net assets|"
    r"(portfolio company )?portfolio investments?( and cash( and cash)? equivalents?)?|"
    r"(portfolio company )?investments? in securities|investments? and cash equivalents?|"
    r"investments? in (non-)?controlled,? (non-)?affiliated portfolio companies|"
    r"investments?-?(non-)?controlled/(non-)?affiliated|investments? portfolio|"
    r".{0,80}% of net assets|"
    r"(total )?(debt|equity|warrant|senior secured|first lien|second lien|other) (investments?|securities)|"
    r"other assets( less|, net of)? (other )?liabilities|liabilities in excess of other assets)"
    r"[\s,\-–—]*(\(?-?\d+(\.\d+)?\s?%\)?)?\s*$",
    re.IGNORECASE,
)


def looks_like_total(identifier: str) -> bool:
    if AGGREGATE_WHOLE_RE.match(identifier):
        return True
    for tok in _SPLIT_RE.split(_PCT_RE.sub(" ", identifier)):
        tok = tok.strip()
        if tok and TOTAL_TOKEN_RE.match(tok) and not STRONG_SUFFIX_RE.search(tok):
            return True
    return False


_PREFIX_SEP_RE = re.compile(r"\s*\|\s*|\s*,\s*|\s+-\s+|\s+–\s+")


def candidate_parents(identifier: str) -> list[str]:
    """Prefixes of the identifier at separator boundaries, e.g.
    'Acme, Inc. | First lien loan 2' -> ['Acme, Inc.', 'Acme, Inc. | First lien loan'].
    Used to find issuer-level subtotal rows that share the prefix."""
    out: list[str] = []
    for m in _PREFIX_SEP_RE.finditer(identifier):
        pre = identifier[: m.start()].strip()
        if pre and pre != identifier:
            out.append(pre)
    m = re.match(r"^(.*\S)\s+\d{1,3}$", identifier)
    if m:
        out.append(m.group(1))
    return list(dict.fromkeys(out))


# ---- main entry ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedIdentifier:
    identifier: str
    issuer_name: str
    issuer_norm: str
    instrument_type: str
    instrument_subtype: str | None
    is_debt: bool
    maturity_text: str | None
    par_amount: float | None
    ident_key: str
    is_total_row: bool


@lru_cache(maxsize=8)
def _industry_re_for(vocab: tuple[str, ...]) -> re.Pattern[str]:
    return _industry_regex(INDUSTRY_VOCAB_DEFAULT + vocab)


_CAMEL_RE = re.compile(r"^[A-Za-z0-9]+Member$")


def _uncamel(ident: str) -> str:
    """'NonaffiliateDebtInvestmentsLifeScienceScientiaVascularIncTermLoanFiveMember' ->
    'Nonaffiliate Debt Investments Life Science Scientia Vascular Inc Term Loan Five'"""
    s = ident.removesuffix("Member")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s


def parse_identifier(identifier: str, extra_industries: tuple[str, ...] = ()) -> ParsedIdentifier:
    ident = identifier.strip()
    if " " not in ident and _CAMEL_RE.match(ident):
        ident = _uncamel(ident)
    industry_re = _industry_re_for(extra_industries) if extra_industries else _INDUSTRY_HEAD_RE
    kv = dict(_KV_RE.findall(ident))
    type_text = " ".join(v.replace("_", " ") for k, v in kv.items() if k in ("TYP", "STY"))
    issuer = extract_issuer(ident, industry_re)
    body = instrument_text(ident, issuer)
    itype, is_debt = "unknown", False
    if "|" in ident:
        for seg in instrument_text(ident, issuer).split("|"):
            if seg.strip():
                itype, is_debt = classify_instrument(seg)
                if itype != "unknown":
                    break
    if itype == "unknown":
        itype, is_debt = classify_instrument(type_text + " " + body)
    if itype == "unknown" and "|" not in ident:
        itype, is_debt = classify_instrument(ident)
    return ParsedIdentifier(
        identifier=ident,
        issuer_name=issuer,
        issuer_norm=normalize_issuer(issuer),
        instrument_type=itype,
        instrument_subtype=classify_subtype(type_text + " " + body),
        is_debt=is_debt,
        maturity_text=extract_maturity_text(ident),
        par_amount=extract_par_amount(ident),
        ident_key=ident_key(ident),
        is_total_row=looks_like_total(ident),
    )
