import { Link } from 'react-router-dom'
import { PageHeader, Section } from '../components/Page'

export default function Method() {
  return (
    <div>
      <PageHeader eyebrow="2 · Methods" title="Methods and calculations" lede="How a filing becomes a clean loan table, how loans are followed across quarters, every metric that is computed and what it means, how the health score is built, and how the strategy was tested. Written so that a reader can check the reasoning, not just the result." />

      <Section title="1. From a filing to a clean loan table">
        <div className="prose">
          <p>The raw files are not a clean table. Each filer writes its loan lines its own way, and tags subtotals, headings, totals and note tables with the same tag as loans. Added up blindly, Ares Capital's lines came to 117% of its own portfolio. About a dozen public filers use a different scheme altogether, describing each holding as a combination of axis members rather than a line of text. The pipeline handles both.</p>
        </div>
        <ol className="steps">
          <li><b>Read every tagged fact per holding.</b> Fair value, cost, principal, rate, spread, PIK rate, maturity, and footnotes, keyed by the holding's identifier text. Only US-dollar facts are used; some filers also tag foreign-currency amounts of the same loan.</li>
          <li><b>Remove what is not a loan.</b> Deterministic rules first: a row whose amount equals the sum of the rows beneath it is a subtotal; a row with the word "total" beside the tranches is a total; the same holding tagged twice is kept once. Then a search: if the remaining rows still overshoot the reported total by more than 5%, groups of rows written in a different shape (a joint venture's schedule, a note table, cash equivalents) are dropped only if that brings the sum back to the total.</li>
          <li><b>Parse the identifier.</b> The borrower's name, the instrument type (first lien, second lien, subordinated, structured, preferred, equity, warrant) and whether it is debt, from text like <code>Icefall Parent, Inc. | First lien senior secured loan</code>.</li>
          <li><b>Reconcile.</b> Sum the fair value of the kept rows and divide by the total investments the BDC printed on its balance sheet. This is the trust gate (section 3).</li>
          <li><b>Link quarters.</b> The same loan is recognised across quarters by exact text, then by normalised text, then by borrower plus instrument and maturity, then by borrower plus instrument class with tranches paired by closest cost, then by fuzzy borrower name. A stable loan id is assigned. Unmatched rows are new loans; loans that disappear are exits, recorded as a loss if the last mark was below 90.</li>
          <li><b>Read the filing itself where the bulk data is short.</b> For filings whose kept rows cover 50 to 97% of the total, the facts are read directly from the filing's inline XBRL and used when they list more holdings. Goldman Sachs BDC's bulk data drops 8 to 12% of its rows every quarter; this recovers them.</li>
        </ol>
        <p className="note">The full list of removal rules, with what each catches and how many rows it caught, is on the <Link to="/data">data page</Link>.</p>
      </Section>

      <Section title="2. What is computed from the loans">
        <div className="prose"><p>All book-level figures are weighted by cost, so a large loan counts more than a small one, and every "share" is a share of the debt book at cost. Equity positions are excluded from credit metrics.</p></div>
        <dl className="defs">
          <dt>mark</dt><dd>fair value / cost, per loan. 1.00 means carried at what was paid; 0.85 means the lender expects to lose 15 cents on the dollar.</dd>
          <dt>share below 90 / 95</dt><dd>share of debt cost carried at a mark under 0.90 or 0.95. The level of stress already admitted.</dd>
          <dt>average mark</dt><dd>total debt fair value / total debt cost. The health of the whole book in one number.</dd>
          <dt>newly stressed</dt><dd>share of debt cost that crossed below 0.95 this quarter. The flow of new trouble.</dd>
          <dt>non-accrual share</dt><dd>share of the book flagged non-accrual in footnotes. A floor: filers only tag it when they choose to.</dd>
          <dt>PIK share</dt><dd>share of debt paying interest in kind. Structural for venture lenders; compare within peer groups.</dd>
          <dt>mark vs peers</dt><dd>on borrowers held by two or more BDCs, this lender's mark minus the average of the others. Its cost-weighted average is a BDC's <b>generosity</b>.</dd>
          <dt>NAV change</dt><dd>NAV per share now versus one and four quarters ago, from the balance-sheet tag.</dd>
          <dt>quality score</dt><dd>peer z-score across nine stress measures, averaged; 0 is the average BDC that quarter, positive is worse. Kept for reference; the tested strategy uses the simpler inputs in section 3.</dd>
        </dl>
      </Section>

      <Section title="3. The health score">
        <div className="prose">
          <p>On the day a BDC files, four of the book metrics are turned into a percentile rank among the liquid public BDCs whose latest filing is already public: the share of loans below 90, the share below 95, the average mark, and the year's NAV change. Each is oriented so that higher means healthier, and the four are averaged.</p>
        </div>
        <span className="formula">{`health = ( (1 - rank(below 90)) + (1 - rank(below 95)) + rank(avg mark) + rank(NAV change 4q) ) / 4
long book  = healthiest fifth      short book = sickest fifth      equal dollars, hold to the next filing`}</span>
        <div className="prose">
          <p>Why these four and not the others: each candidate metric was tested on its own the same way (section 4). These four ranked the next holding period correctly in nearly every quarter; non-accruals, price to NAV, momentum and cross-lender generosity did not. Forty variations (one to seven inputs, five to twelve names a side, equal or rank-weighted sizing) all work; this one was chosen for robustness, not because it was the best-looking line.</p>
        </div>
      </Section>

      <Section title="4. How the strategy was tested">
        <ol className="steps">
          <li><b>Enter on the filing day.</b> Each BDC is scored the day after its own quarterly report is public, using only that report and the other BDCs' reports already public that day.</li>
          <li><b>Rank against peers.</b> Four inputs, each turned into a percentile among the liquid public BDCs, averaged into a health score.</li>
          <li><b>Take positions.</b> The healthiest fifth is bought, the sickest fifth sold short, equal dollars, held until each name's next report (about 91 days).</li>
          <li><b>Measure against the sector.</b> Each return, dividends included, has the equal-weight return of all other liquid BDCs with a trusted quarter over the same window subtracted. The result is the quality spread, not BDC beta.</li>
          <li><b>Exclude what cannot be traded.</b> A name counts on a date only if it traded at least $100,000 a day over the prior six months.</li>
          <li><b>Repeat for every quarter since late 2022.</b> The record is shown quarter by quarter, not as an average alone. One quarter was rebuilt from scratch with separate code and matched.</li>
        </ol>
        <p className="note">The results are on the <Link to="/results">results page</Link>; the loan-level warning-score tests are under <Link to="/validation">signal tests</Link>.</p>
      </Section>

    </div>
  )
}
