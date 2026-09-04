import pytest

from soi.transform.parse_identifier import looks_like_total, normalize_issuer, parse_identifier

CASES = [
    ("15484880 Canada Inc. | First lien senior secured loan 1", "15484880 Canada Inc", "first_lien", True),
    (
        "15484880 Canada Inc. and 15484910 Canada Inc. | Class A2 shares",
        "15484880 Canada Inc. and 15484910 Canada Inc", "equity", False,
    ),
    (
        "Debt Investments, Application Software, Alchemer LLC, Senior Secured, May 2028, "
        "1-month SOFR + 8.14%, Floor rate 9.14%",
        "Alchemer LLC", "first_lien", True,
    ),
    (
        "Control investments - 10.9% - Pepper Palace, Inc. - Specialty Food Retailer - "
        "First Lien Term Loan 4.42% PIK, 1",
        "Pepper Palace", "first_lien", True,
    ),
    (
        "Mood Media Borrower, LLC, Debt investments, First Lien Term Loan|ISS=Mood_Media_Borrower_LLC "
        "TYP=Debtinvestments STY=First_Lien_Term_Loan GEO=US",
        "Mood Media Borrower LLC", "first_lien", True,
    ),
    (
        "PHYSICIAN PARTNERS, LLC - Healthcare & Pharmaceuticals - Term Loan B1 (1/25) - Loan",
        "PHYSICIAN PARTNERS", "first_lien", True,
    ),
    ("Acme Holdings, L.P. | Second lien senior secured loan", "Acme Holdings", "second_lien", True),
    ("Acme Corp | Preferred equity", "Acme Corp", "preferred", False),
    ("Acme Corp | Warrants", "Acme Corp", "warrant", False),
    ("Acme Corp | Subordinated notes", "Acme Corp", "subordinated", True),
    (
        "Investments United States Debt Investments Software & Services Benesys Inc. "
        "Investment Type Senior Secured Interest Rate 10.5% Maturity 5/13/2030",
        "Benesys Inc", "first_lien", True,
    ),
    (
        "Investment Debt Investments - 233.2% United States - 220.5% 1st Lien/Senior Secured Debt "
        "- 206.5% VRC Companies, LLC",
        "VRC Companies", "first_lien", True,
    ),
    (
        "Non-controlled/Non-Affiliated Investments Beverage, Food & Tobacco SauceCo HoldCo, LLC "
        "First Lien Senior Secured Loan - Revolver SOFR Spread 5.75% Interest Rate 9.42% "
        "Maturity Date 5/13/2030",
        "SauceCo HoldCo", "first_lien", True,
    ),
    ("ABB/Con-cise Optical Group LLC First Lien Secured Term Loan", "ABB/Con-cise Optical Group LLC",
     "first_lien", True),
    (
        "Non-control/Non-affiliate investments - 273.4% - ComForCare Health Care - Healthcare Services "
        "- First Lien Term Loan",
        "ComForCare Health Care", "first_lien", True,
    ),
    ("Titan BW Borrower L.P. | One stop 3 | Non-Affiliated Issuer", "Titan BW Borrower L.P",
     "first_lien", True),
    ("Sphera Solutions Inc | Software & Services 1", "Sphera Solutions Inc", "unknown", False),
    # co-borrower names containing instrument words must not drive classification
    ("Absolute Dental Group LLC and Absolute Dental Equity, LLC | First lien senior secured loan",
     "Absolute Dental Group LLC and Absolute Dental Equity LLC", "first_lien", True),
    ("Zeppelin US Buyer Inc. and Providence Equity Partners IX-C L.P. | First lien senior secured loan 1",
     "Zeppelin US Buyer Inc. and Providence Equity Partners IX-C L.P.", "first_lien", True),
    ("Diligent Corporation and Diligent Preferred Issuer, Inc. | First lien senior secured revolving loan",
     "Diligent Corporation and Diligent Preferred Issuer Inc.", "first_lien", True),
    ("Harris Preston Fund Investments | LP Interests (HPEP 3, L.P.)", "Harris Preston Fund Investments", "equity", False),
    ("EIG Fund Investments | LP Interests (EIG Global Private Debt Fund-A, L.P.)", "EIG Fund Investments", "equity", False),
    ("Windows Entities | LLC Units", "Windows Entities", "equity", False),
    ("Packaging Coordinators Midco, Inc. | First lien senior secured loan | Non-Affiliated Issuer",
     "Packaging Coordinators Midco Inc.", "first_lien", True),
    ("MS Private Loan Fund I, LP | LP Interests", "MS Private Loan Fund I LP", "equity", False),
    ("Ivy Hill Asset Management, L.P. | Member interest", "Ivy Hill Asset Management L.P.", "equity", False),
    ("RL Datix Holdings (USA), Inc. | First lien senior secured loan", "RL Datix Holdings Inc.", "first_lien", True),
    ("Integrity Marketing Acquisition, LLC |First lien senior secured loan | Non-Affiliated Issuer",
     "Integrity Marketing Acquisition LLC", "first_lien", True),
]


@pytest.mark.parametrize("ident,issuer_startswith,itype,is_debt", CASES)
def test_parse(ident, issuer_startswith, itype, is_debt):
    p = parse_identifier(ident)
    assert p.issuer_name.startswith(issuer_startswith), p
    assert p.instrument_type == itype, p
    assert p.is_debt is is_debt, p


def test_normalize():
    assert normalize_issuer("Pepper Palace, Inc.") == "pepper palace"
    assert normalize_issuer("Mood Media Borrower, LLC") == "mood media"
    assert normalize_issuer("Acme Holdings, L.P. (dba Acme)") == "acme"


def test_maturity_text():
    assert parse_identifier("Acme LLC | First lien loan, due 3/2029").maturity_text == "3/2029"
    assert parse_identifier("Debt Investments, Software, Alchemer LLC, Senior Secured, May 2028, SOFR + 8%").maturity_text == "May 2028"
    assert parse_identifier("X Inc First Lien Maturity Date 5/13/2030").maturity_text == "5/13/2030"


@pytest.mark.parametrize(
    "ident,expected",
    [
        ("Sub Total Non-control/Non-affiliate investments", True),
        ("Non-control/Non-affiliate investments - 273.4% - Total Consumer Services", True),
        ("Total Investments (211.5%)", True),
        ("Investments after Cash Equivalents", True),
        ("Cash Equivalents, Net Assets 26.3%", True),
        ("Largest Portfolio Company Investment", True),
        ("Investments United States Debt Investments Software & Services Benesys Inc.", False),
        ("Total Safety Holdings LLC | First lien loan", False),
        ("Acme Corp | Common stock", False),
    ],
)
def test_total(ident, expected):
    assert looks_like_total(ident) is expected
