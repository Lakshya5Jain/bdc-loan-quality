import { Link } from 'react-router-dom'
import { Explain, PageHeader, Section } from '../components/Page'
import Stat from '../components/Stat'
import { useApi } from '../lib/api'
import { signedPct } from '../lib/format'

type Summary = { n_quarters: number; quarters_won: number; mean_spread: number | null; worst_spread: number | null; n_names_now: number; n_long_now: number; latest_period: string; latest_entry_date: string }
type Counts = { counts: { holdings: number; loans: number; bdcs: number; screened: number; latest_period: string; latest_price_date: string | null } }

export default function Overview() {
  const strat = useApi<{ summary: Summary; signals: { signal: string; mean_spread_dir: number | null; n_periods: number }[] }>('/api/strategy')
  const lab = useApi<{ variants: { variant: string; mean_net: number; quarters_won_net: number; n_quarters: number }[] }>('/api/lab')
  const v2m = lab.data?.variants.find((v) => v.variant === 'floor_2m')
  const navAlone = strat.data?.signals.find((x) => x.signal === 'nav_chg_4q')
  const status = useApi<Counts>('/api/status')
  const s = strat.data?.summary
  const c = status.data?.counts
  return (
    <div>
      <PageHeader eyebrow="Start here" title="BDC Investment Strategy" lede="A long/short strategy on publicly traded business development companies, built entirely from the loan-by-loan schedules they file with the SEC every quarter. This page explains the goal, the idea, and the process from raw filing to trade, step by step. The pages that follow hold the data, the calculations, the results and the current book." />

      <Section title="The goal">
        <div className="prose">
          <p>A business development company (BDC) is a listed company whose whole business is lending to mid-sized private companies. It is a bucket of loans with a stock price on it. Two numbers matter: <b>NAV</b>, what the company says its loans are worth minus what it owes, and the <b>stock price</b>, what the market pays for a share.</p>
          <p>Every quarter each BDC must publish what every single loan it holds is worth. When loans go bad, the BDC writes them down, NAV falls, the dividend gets cut, and the stock drops. The goal of this project is to read those loan lists carefully and early, for every BDC at once, and use them to tell which BDCs are heading for trouble and which are healthy but cheap.</p>
          <p><b>The strategy in one sentence:</b> every time a BDC reports, rank it against the others on how sick its loan book is; own the healthiest fifth, bet against the sickest fifth, and hold until the next report.</p>
        </div>
      </Section>

      {s && c && (
        <div className="stats">
          <Stat k="BDCs in the data" v={c.bdcs} d="public and private, quarterly since 2022" />
          <Stat k="Loan-quarters" v={c.holdings.toLocaleString()} d="one row per loan per quarter" />
          <Stat k="Quarters tested" v={`${s.quarters_won} of ${s.n_quarters}`} d="positive, long minus short" cls="pos" />
          <Stat k="Long minus short" v={signedPct(s.mean_spread)} d="average per quarter, before costs" cls="pos" />
          {v2m && <Stat k="Tradable, after costs" v={signedPct(v2m.mean_net)} d={`$2m-a-day names, net of trading and borrow · ${v2m.quarters_won_net} of ${v2m.n_quarters} up`} cls={v2m.mean_net > 0 ? 'pos' : 'neg'} />}
          <Stat k="Book today" v={`${s.n_long_now} / ${s.n_long_now}`} d={`long / short of ${s.n_names_now} liquid names`} />
          <Stat k="Latest filings" v={s.latest_period} d={`prices ${s.latest_entry_date}`} />
        </div>
      )}

      <Section title="The process, step by step">
        <ol className="steps">
          <li><b>Collect every filing.</b> The SEC publishes each BDC's tagged schedule of investments as bulk data files. We load every file for every BDC that files, about 200, public and private, back to late 2022. Private BDCs matter because they hold the same loans as the public ones and give a second opinion on every mark. <Link to="/data">See the data.</Link></li>
          <li><b>Turn the filings into one clean loan table.</b> The raw files mix real loans with subtotals, headings, totals and note tables, and every filer writes its lines its own way. A set of rules removes what is not a loan, then a check: the loans we keep must add up to the total the BDC printed on its balance sheet. Quarters that fail that check are never used. <Link to="/methods">See the methods.</Link></li>
          <li><b>Follow each loan across quarters.</b> The same loan is recognised from one report to the next even when the wording changes, so we can see it being marked down over time, going on non-accrual, switching to payment in kind, or being sold at a loss.</li>
          <li><b>Score every loan and every book.</b> Each loan gets a warning score fitted on our own history: how often loans that looked like this went bad within a year. Each BDC's book is summarised by how much of it is already impaired, how much is newly going wrong, and how its marks compare with other lenders holding the same borrowers. <Link to="/methods">See the calculations.</Link></li>
          <li><b>Rank the public BDCs.</b> On the day a BDC files, it is ranked against the other liquid public BDCs on four things: share of loans below 90 cents, share below 95, the average mark of the book, and the year's NAV change. The four ranks average into one health score.</li>
          <li><b>Test it honestly.</b> Replay every quarter since late 2022 using only what was public that day: buy the healthiest fifth, short the sickest fifth, hold to the next report, measure against the sector. Report every quarter, not just the average. <Link to="/results">See the results.</Link></li>
          <li><b>Publish the book.</b> The current long and short lists, with the four numbers behind each name and a link to every loan. <Link to="/book">See the strategy today.</Link></li>
        </ol>
      </Section>

      <Section title="What we found, in brief">
        <Explain kind="info">
          <p><b>The sickness of the book predicts the stock.</b> Ranking BDCs by the share of loans already marked below 90 cents on the dollar ranked the next quarter correctly in every one of the fifteen quarters tested. The combined health score won {s ? `${s.quarters_won} of ${s.n_quarters}` : '15 of 15'}, by about {s ? signedPct(s.mean_spread) : '4.5%'} per quarter between the healthy and sick groups, net of the sector and before costs.</p>
          <p><b>The number everyone quotes does not.</b> Headline non-accruals, price to NAV and recent momentum carried no information about the next quarter on their own. The marks lead; the labels lag. One caveat in the other direction: the one-year NAV change, which every press release carries, did about as well on its own{navAlone && navAlone.mean_spread_dir != null ? ` (${signedPct(navAlone.mean_spread_dir)} per quarter over ${navAlone.n_periods} quarters)` : ''}; the loan-level inputs add consistency and an earlier read rather than a bigger average.</p>
            <p><b>What a fund would actually keep.</b> The gross spread is before costs and includes shorts in tiny names that are expensive to borrow. Restricted to names trading $2m a day and charged for trading and borrow, the strategy kept {v2m ? signedPct(v2m.mean_net) : 'less'} a quarter. That is the honest number; the <Link to="/results">results page</Link> shows every variant.</p>
          <p><b>What could still be wrong.</b> Fifteen quarters is one credit cycle; each side of the book holds about nine stocks; BDCs delisted since 2022 are missing from the test; returns are before the cost of borrowing stock to short. <Link to="/results">Every caveat is on the results page.</Link></p>
        </Explain>
      </Section>

      <Section title="How to read this site">
        <div className="prose">
          <ol>
            <li><Link to="/data">Data</Link>: where the numbers come from, how much of it there is, and the trust gate that decides which quarters are used.</li>
            <li><Link to="/methods">Methods and calculations</Link>: how a filing becomes a loan table, every metric defined, and how the strategy was tested.</li>
            <li><Link to="/results">Results</Link>: the strategy's record quarter by quarter, and how each input performed on its own.</li>
            <li><Link to="/book">Strategy today</Link>: the current long and short books.</li>
            <li>Explore: <Link to="/bdcs">every BDC</Link>, <Link to="/borrowers">every borrower</Link> across lenders, the <Link to="/screener">screener</Link>, <Link to="/vintages">vintages</Link> and <Link to="/scorecards">lender track records</Link>.</li>
            <li><Link to="/glossary">Glossary</Link>: every term in plain English. Hover any column header on any page for the same definition.</li>
          </ol>
          <p>Every number on the site links back to the loans behind it. Click a ticker to see the book; click a loan to follow it across quarters and see every other lender's mark on it.</p>
        </div>
      </Section>

      <Section title="About">
        <div className="prose">
          <p>Made by <b>Laksh Jain</b>, <a href="mailto:ljain@princeton.edu">ljain@princeton.edu</a>. Data: SEC EDGAR XBRL filings and public market prices. This is a research tool, not investment advice.</p>
        </div>
      </Section>
    </div>
  )
}
