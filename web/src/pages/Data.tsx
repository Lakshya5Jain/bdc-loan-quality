import { Link } from 'react-router-dom'
import { Explain, PageHeader, Section } from '../components/Page'
import Stat from '../components/Stat'
import { useApi } from '../lib/api'

type S = {
  files: { source_file: string; loaded_at: string; n_sub: number; n_num: number; n_txt: number }[]
  counts: { holdings: number; loans: number; bdcs: number; screened: number; latest_period: string; latest_price_date: string | null }
  reconciliation: { status: string; n: number }[]
  excluded: { reason: string; n: number }[]
}

export default function Data() {
  const { data, error, loading } = useApi<S>('/api/status')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const recon = Object.fromEntries(data.reconciliation.map((r) => [r.status, r.n]))
  const total = data.reconciliation.reduce((a, r) => a + r.n, 0)
  return (
    <div>
      <PageHeader eyebrow="1 · Data" title="The data" lede="What is in the database, where it comes from, and the check that decides whether a quarter of a BDC's data can be trusted. Everything else on the site is built on what passes this page." />

      <div className="stats">
        <Stat k="BDCs with holdings" v={data.counts.bdcs} d="public and private" />
        <Stat k="Public BDCs screened" v={data.counts.screened} d="liquid, with a trusted latest quarter" />
        <Stat k="Loan-quarters" v={data.counts.holdings.toLocaleString()} d="one row per loan per quarter" />
        <Stat k="Loans tracked" v={data.counts.loans.toLocaleString()} d="linked across quarters" />
        <Stat k="Latest filings" v={data.counts.latest_period} />
        <Stat k="Latest prices" v={data.counts.latest_price_date ?? '—'} />
      </div>

      <Section title="Where it comes from">
        <div className="prose">
          <p><b>Loan schedules.</b> Every business development company files a quarterly report with the SEC that includes its full schedule of investments: one line per loan, with the borrower's name, the type of loan, what the BDC paid for it (cost), what it says the loan is worth today (fair value), the interest rate, and footnotes such as "on non-accrual". Since 2022 the SEC has required these schedules to be tagged in XBRL and publishes them as monthly bulk data files. We load every file, for every BDC, public and private.</p>
          <p><b>Why private BDCs are kept.</b> They cannot be traded, but they hold the same loans as the public ones. When five lenders hold a slice of one borrower, the four we cannot trade are evidence about the one we can: if they have all marked the loan down and the public one has not, that is a signal.</p>
          <p><b>Income statement and balance sheet.</b> Net investment income, net assets, borrowings, shares and NAV per share come from the same filings, tagged without a holding identifier.</p>
          <p><b>Prices and dividends</b> come from public market data, daily, for every listed BDC. Closing prices were spot-checked against Nasdaq's own historical data and matched to the cent; dividend counts and totals match the companies' payout schedules.</p>
          <p><b>The filings themselves.</b> Where the bulk data misses rows (Goldman Sachs BDC loses 8 to 12% of its lines every quarter in the bulk files), the facts are read directly from the filing's inline XBRL.</p>
        </div>
      </Section>

      <Section title="The trust gate" meta={`${total.toLocaleString()} BDC-quarters`}>
        <Explain><p><b>Coverage</b> is our sum of loan fair values divided by the total investments the BDC itself printed on its balance sheet. A quarter is <b>trusted</b> when coverage is between 0.90 and 1.10, or when a documented override applies (one filer nets three lines out of its reported total). Nothing downstream uses an untrusted quarter: no score, no screen row, no backtest. This is why a number on the site can be wrong only in the way the filing is wrong.</p></Explain>
        <div className="kv">
          <div><span>Reconciles</span><span>{(recon.reconciles ?? 0).toLocaleString()}</span></div>
          <div><span>Our detail under the reported total</span><span>{(recon.under ?? 0).toLocaleString()}</span></div>
          <div><span>Our detail over the reported total</span><span>{(recon.over ?? 0).toLocaleString()}</span></div>
          <div><span>No comparable total in the filing</span><span>{(recon['no total'] ?? 0).toLocaleString()}</span></div>
        </div>
        <p className="note">For the public BDCs, 97% of quarters pass the gate and most sit within 1%. The latest quarter of every screened name was also checked line by line against the filing's inline XBRL: over 99% of holdings carry the exact fair value shown in the filing. Each BDC's coverage by quarter is on <Link to="/bdcs">its own page</Link> under "Data check".</p>
      </Section>

      <Section title="What is removed before a row counts as a loan" meta={`${data.excluded.reduce((a, r) => a + r.n, 0).toLocaleString()} rows removed`}>
        <div className="prose"><p>Filers tag subtotals, headings, totals and tables copied from footnotes with the same tag as loans. Each removal rule is listed with what it catches and how many rows it caught across every filing. The rules are explained on the <Link to="/methods">methods page</Link>.</p></div>
        <div className="tablewrap">
          <table className="grid">
            <thead><tr><th className="l">Rule</th><th className="l">What it removes</th><th>Rows</th></tr></thead>
            <tbody>{data.excluded.map((r) => <tr key={r.reason}><td className="l"><code>{r.reason}</code></td><td className="l" style={{ whiteSpace: 'normal', fontFamily: 'var(--font-body)' }}>{EXCLUDE_LABEL[r.reason] ?? ''}</td><td>{r.n.toLocaleString()}</td></tr>)}</tbody>
          </table>
        </div>
      </Section>

      <Section title="What the source cannot tell you">
        <Explain kind="warn">
          <p><b>Maturity dates</b> are missing for about fifteen BDCs, including Ares Capital and Blue Owl, because they do not tag them. <b>Non-accrual</b> exists only where the filer footnoted it, so counts are a floor. <b>History</b> starts in late 2022: fifteen quarters, one credit cycle. <b>Delisted BDCs</b> since 2022 have no free price history and are absent from the backtest.</p>
        </Explain>
      </Section>

      <Section title="Source files loaded">
        <div className="tablewrap">
          <table className="grid">
            <thead><tr><th className="l">SEC bulk file</th><th className="l">Loaded</th><th>Filings</th><th>Numeric facts</th><th>Text facts</th></tr></thead>
            <tbody>{data.files.map((f) => <tr key={f.source_file}><td className="l"><code>{f.source_file}</code></td><td className="l">{f.loaded_at.slice(0, 16)}</td><td>{f.n_sub}</td><td>{f.n_num.toLocaleString()}</td><td>{f.n_txt.toLocaleString()}</td></tr>)}</tbody>
          </table>
        </div>
        <p className="note">Files are named by filing month, not fiscal quarter: the August file carries the June-quarter reports.</p>
      </Section>
    </div>
  )
}

const EXCLUDE_LABEL: Record<string, string> = {
  no_values: 'rows with neither cost nor fair value (unfunded commitments, footnote prose)',
  total_row: '"Total investments" and category total rows',
  issuer_subtotal: 'per-issuer subtotals that equal the sum of the tranches beneath them',
  heading_subtotal: 'industry or category headings whose amount equals the rows beneath them',
  heading: 'headings dropped because the detail otherwise overshot the reported total',
  heading1: 'headings with a single row beneath them',
  heading_bare: 'headings with no amounts of their own',
  member_category: 'category breakdowns (by industry, type or affiliation) tagged like holdings',
  note_schedule: 'joint-venture or affiliate schedules copied from the notes',
  no_cost_nonpositive_fv: 'zero or negative fair value with no cost (unfunded commitment marks)',
  punct_dupe: 'the same holding tagged twice under punctuation variants',
  fv_only_twin: 'a fair-value-only twin of a row that carries cost',
  issuer_sum: 'issuer total rows written in a different format from the tranches',
  issuer_total_row: '"Total <issuer>" rows beside the tranches',
  legal_entity_dupe: 'the same holding reported both consolidated and per legal entity',
  unpiped_issuer_row: 'issuer-level aggregates in pipe-format filings',
  filing_total: 'rows equal to the filing total',
  total_token: 'rows containing the word "total"',
  industry_name: 'rows that are only an industry name',
  pct_heading: 'headings ending in a percent of net assets',
  nodigit: 'category rows without a single digit',
  bare: 'rows with no instrument-level facts',
  cash_equiv: 'money market and treasury bill rows when the reported total excludes them',
  no_cost: 'fair-value-only rows in filings that tag cost everywhere else',
  no_footnote: 'rows without footnotes in filings that footnote every real holding (a second schedule)',
  amount_dupe: 'the same issuer and amount tagged under another wording',
  name_list: 'note tables listing portfolio companies by industry',
}
