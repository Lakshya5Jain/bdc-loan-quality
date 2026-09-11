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

      <Section title="5. Stale marks: the same loan, valued by different lenders">
        <div className="prose">
          <p><b>Why this exists.</b> A loan does not trade, so its value is one lender's opinion. But many private companies borrow from several BDCs at once, and each BDC files its own opinion of the same loan every quarter, without coordinating. That gives a free second opinion on every shared loan. When two lenders disagree, at least one is wrong, and in practice the higher mark is usually the one that has not caught up yet. This section measures the disagreement and asks whether lenders that habitually mark high pay for it later. Nothing here feeds the health score.</p>
          <p><b>Worked example.</b> Three BDCs hold the first-lien loan of the same company. In the June quarter lender A marks it at 0.99, lender B at 0.95 and lender C at 0.60. The <b>gap</b> is 0.99 minus 0.60 = 0.39. Lender A's slice cost $10m and the other two slices cost $20m combined at an average mark of 0.72, so on this loan lender A is 0.27 above the crowd. Do that for every loan A shares, weight by what each slice cost, and the average is A's <b>generosity</b>. If A also holds $300m of loans nobody else owns out of a $500m book, its <b>no-second-opinion share</b> is 60%: that part of the book cannot be checked against anyone.</p>
          <p><b>Rules.</b> A first lien is only compared with other lenders' first liens on the same company, never with a second lien, so the unit is one borrower, one lien type, one quarter. A few private filers report fair value and cost in different units, which produces marks of 3 or 8; any position marked outside 0 to 1.25 is dropped as a filing error, not an opinion. Generosity on fewer than five shared positions is noise and is not used in the test.</p>
          <p><b>The test.</b> Every calendar quarter the public BDCs with five or more shared positions are sorted on generosity. The most generous fifth and the least generous fifth are then followed for one and two quarters on two outcomes: how NAV per share changed, and how the share of loans marked below 90 changed. If generous marks are stale, the generous group should see NAV fall more and stress rise more. Each quarter is one observation; the t-statistic is the average difference divided by its standard error across quarters, and the hit rate is the share of quarters with the expected sign. Because generous lenders often already have more stressed books, each group's starting share below 90 is shown next to the outcome, so a rise that is just existing trouble continuing is not mistaken for stale marks catching up.</p>
          <p><b>What it found.</b> Generosity told us nothing about NAV, and if anything ran the other way. It did line up with a later rise in loans below 90, in most quarters. The exact numbers, per quarter, are on the <Link to="/stale-marks">stale marks page</Link>.</p>
        </div>
      </Section>

      <Section title="6. Strategy lab: what survives costs, size and a factor check">
        <div className="prose">
          <p><b>Why this exists.</b> The default strategy in section 4 is before costs and holds anything that trades at least $100,000 a day. A fund cannot run that: small BDCs are expensive to borrow for shorting, and a 4.5% paper spread can be 1.5% in practice. The lab reruns the exact same strategy under conditions a fund would face, and checks whether the score is real information or a value factor in disguise. None of these variants replaces the default; they are shown side by side on the <Link to="/results">results page</Link>.</p>
          <p><b>Costs.</b> Two charges per holding period. Trading: 40 basis points per round trip on each leg, so 80 on the long/short pair. Borrow: to short a stock you must borrow it and pay a fee. Small, hard-to-borrow BDCs cost 15% a year, mid-sized ones 5%, large ones 1%; the tier is set by how many dollars a day the name trades (under $1m, $1m to $5m, over $5m), and the fee is pro-rated to the days held. These are rough tiers, not broker quotes. Example: a 91-day short in a name trading $400k a day costs 15% x 91/365 = 3.7% for that position.</p>
          <p><b>Liquidity floor.</b> The book may only hold names trading more than the floor per day: $100k (the default), $1m, $2m or $5m. Every BDC is still ranked against every liquid peer, so a health score of 0.8 means the same thing at every floor; the floor only decides which names the book is allowed to hold. Fewer names means a thinner book, so the $5m version has some quarters with too few names to trade.</p>
          <p><b>Residual score.</b> Cheap stocks tend to be the ones with bad books, so a score that likes good books also, quietly, likes expensive stocks. To separate the two, each quarter the health score is regressed on price / NAV across all names and only the leftover is ranked. If the leftover still works, the score carries information the market price does not; if it stops working, the strategy was just a value bet. It kept most of its edge.</p>
          <p><b>Conviction weighting.</b> Instead of equal dollars in each of the nine longs and nine shorts, dollars go in proportion to how far each name's score sits from the middle of the pack. The strongest calls get the most money. It changed little.</p>
          <p><b>Fifth inputs.</b> Three candidates were tried as an extra peer-rank averaged into the score: generosity from section 5, and two roll-ups of the loan warning score, the cost-weighted average score and the share of the book scored 30 or more. The loan score is refitted every quarter using only loan-quarters whose one-year outcome was already known on the scoring date, so nothing from the future leaks in. Each was also tested on its own, the same way as the four default inputs.</p>
          <p><b>Turnover.</b> The share of each side's book that changed names since the previous quarter, averaged over the two sides. 0.25 means a quarter of the positions turned over, which is what the trading cost is charged on.</p>
        </div>
      </Section>

      <Section title="7. Forced sellers: distance to the leverage limit">
        <div className="prose">
          <p><b>Why this exists.</b> A BDC funds its loans partly with borrowed money, and the law caps that. Asset coverage, (net assets + debt) / debt, must stay above 150%. Below it the BDC cannot borrow more and cannot pay dividends, so it has to raise equity or sell loans, and a seller that has to sell takes whatever price it is offered. A BDC that is close to the limit while its loans are getting worse is the one most likely to sell at a loss. Shown as a column on the <Link to="/screener">screener</Link> and a section on each BDC page.</p>
          <p><b>Worked example.</b> A BDC has $1.0bn of net assets and $1.4bn of debt. Coverage is (1.0 + 1.4) / 1.4 = 171%, so its <b>distance</b> is 21 points above the minimum. If it writes down $100m of loans, net assets fall to $0.9bn and coverage to 164%: a 7% loss of NAV cost it a third of its headroom. That is why 20 points is the line for "near the limit".</p>
          <p><b>Where the number comes from.</b> About a third of the time the filer tags the ratio itself and that is used. A tagged value of exactly 1.50 or 2.00 is the legal minimum being tagged, not the actual ratio, and is ignored. Otherwise it is computed from net assets and debt on the balance sheet. Values outside 1.2 to 6 are dropped as tagging errors.</p>
          <p><b>The flag.</b> Within 20 points of the minimum AND the share of loans below 90 rose in each of the last two quarters. Both halves have to be true.</p>
          <p><b>The test.</b> Flagged BDC-quarters against all others on one outcome: the cost of loans that left the book over the next two quarters with a last mark below 0.90, as a share of the debt book. That is the footprint of selling at a loss. The difference in means comes with a 95% interval from resampling the quarters, and each half of the flag is tested on its own so it is clear which part carries the result. The answer: flagged quarters were followed by about two and a half times the loss exits, with proximity to the limit doing most of the work.</p>
        </div>
      </Section>

      <Section title="8. Neighbours of distress">
        <div className="prose">
          <p><b>Why this exists.</b> When a borrower goes bad, the natural question is which other loans look like it, because a lender's book is full of loans nobody has re-examined lately. This is the "similar loans" search: for every fresh distress event, find the clean loans whose terms most resemble what the failed loan looked like just before it failed, and check whether those loans really do go bad more often than any other clean loan. Results and the watchlist are on the <Link to="/neighbors">neighbours page</Link>.</p>
          <p><b>The profile.</b> Every borrower, every quarter, summed across all the lenders that hold it: how much of it is first lien, the cost-weighted interest rate and spread, how much is paying interest in kind, months to maturity, the size of the loan (log of total cost), how many lenders hold it, and a broad sector where any lender tagged an industry. Only about 13% of borrower-quarters have an industry tag, because most filers do not tag one, so sector applies to a minority of matches. Each feature is standardised within the quarter so that a one-point spread difference and a $100m size difference are on the same scale.</p>
          <p><b>Distress event.</b> The first quarter a borrower's mark (summed across lenders) drops below 0.90 or any lender puts it on non-accrual, after a quarter in which it was above 0.90 and accruing. Borrowers that first appear already distressed are not events; the point is the transition.</p>
          <p><b>Neighbours.</b> For each event, the ten borrowers still marked 0.97 or better whose profile is closest to the event borrower's profile from the quarter before it went bad. Distance is the root-mean-square difference over the features both borrowers have, with at least three needed; the mark is not one of them, since every candidate is near par by construction. Candidates in the same sector come first when the event borrower's sector is known.</p>
          <p><b>Controls.</b> For the same event, ten borrowers drawn at random from the same near-par pool with the same lien bucket. This turns the question into "does a similar profile matter" rather than "do clean loans ever go bad".</p>
          <p><b>The test.</b> The share of neighbours marked below 0.95 within four quarters, against the same share for controls. A pair only counts if the neighbour was seen again at least nine months later or fell first, and events too recent for that window to have passed are left out, otherwise the only pairs that could count would be the ones that already fell. Because one event contributes ten pairs, the 95% interval comes from resampling events, not pairs. The answer: neighbours fell below 0.95 about a fifth more often than random clean loans, a real but modest edge, and matching on sector made it worse.</p>
        </div>
      </Section>
    </div>
  )
}
