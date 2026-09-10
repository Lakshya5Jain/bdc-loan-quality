import { useState } from 'react'
import { Link } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'
import { mm, num } from '../lib/format'
import { G } from '../lib/glossary'

type Loan = { list: string; loan_id: string; cik: number; ticker: string | null; bdc_name: string; is_public: boolean; issuer_name: string; borrower_key: string; industry: string | null; instrument_type: string; period_end: string; cost: number; fair_value: number; mark: number | null; risk_score: number; reasons: string[]; n_bdcs: number | null; peer_avg_mark: number | null; mark_vs_peers: number | null }
type W = { loans: Loan[] }

const LISTS: Record<string, [string, string]> = {
  deteriorating: ['Deteriorating loans', 'Highest estimated chance of non-accrual or loss within four quarters, and not yet on non-accrual. The loans most likely to be the next markdowns.'],
  marked_above_peers: ['Marked above other lenders', 'The same borrower is held by two or more BDCs and this lender marks it at least 5 points higher than the others. Either it knows something, or it is late.'],
  marked_below_peers: ['Marked below other lenders', 'This lender is the most conservative on the name. Either an early warning for the other holders, or a cheap mark.'],
  single_lender_rich: ['Single lender, marked at par, elevated risk', 'No second opinion on the mark, and the loan shows the warning signs that precede trouble.'],
}

export default function Insights() {
  const { data, error, loading } = useApi<W>('/api/insights/watchlists')
  const [list, setList] = useState('deteriorating')
  const [publicOnly, setPublicOnly] = useState(true)
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const loans = data.loans.filter((l) => l.list === list && (!publicOnly || l.is_public))
  const loanCols: Col<Loan>[] = [
    { header: 'Borrower', accessorKey: 'issuer_name', left: true, cell: (c) => <Link to={`/borrowers/${encodeURIComponent(c.row.original.borrower_key)}`}>{c.getValue<string>()}</Link> },
    { header: 'Lender', accessorKey: 'ticker', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.getValue<string>() ?? c.row.original.bdc_name}</Link> },
    { header: 'Instrument', accessorKey: 'instrument_type', left: true, cell: (c) => <Link to={`/loans/${c.row.original.loan_id}`}>{c.getValue<string>().replace(/_/g, ' ')}</Link> },
    { header: 'Cost $mm', accessorKey: 'cost', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Mark', accessorKey: 'mark', tip: G.mark, cell: (c) => <span className={c.getValue<number>() != null && c.getValue<number>() < 0.95 ? 'neg' : ''}>{num(c.getValue<number>(), 3)}</span> },
    { header: 'Lenders', accessorKey: 'n_bdcs', tip: 'Number of BDCs holding this borrower.' },
    { header: 'Peers\' avg mark', accessorKey: 'peer_avg_mark', tip: 'Average mark across all BDCs holding this borrower.', cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'Risk score', accessorKey: 'risk_score', tip: G.risk, cell: (c) => <b className={c.getValue<number>() >= 40 ? 'neg' : ''}>{c.getValue<number>()}</b> },
    { header: 'Why', accessorKey: 'reasons', left: true, wrap: true, cell: (c) => (c.getValue<string[]>() ?? []).join('; ') },
  ]
  return (
    <div>
      <PageHeader eyebrow="Explore" title="Watchlists" lede="Individual loans worth a look this quarter, chosen by the loan-level warning score or by disagreement between lenders holding the same borrower. Every row says why it is there." />
      <Explain>
        <p><b>How to read this.</b> The <b>risk score</b> is the estimated chance, 0 to 100, that a loan goes on non-accrual, is marked below 80, or leaves the book at a loss within four quarters. It is fitted on this database's own history: each warning sign's weight is how much more often loans with that sign went bad (see <Link to="/validation">signal tests</Link>). These lists are for reading a book loan by loan; the BDC-level strategy is on the <Link to="/book">strategy today</Link> page.</p>
      </Explain>
      <Section title={LISTS[list][0]} meta={`${loans.length} loans`}>
        <div className="panel">
          <div className="controls">
            <select value={list} onChange={(e) => setList(e.target.value)}>
              {Object.entries(LISTS).map(([k, [label]]) => <option key={k} value={k}>{label}</option>)}
            </select>
            <label><input type="checkbox" checked={publicOnly} onChange={(e) => setPublicOnly(e.target.checked)} /> public BDCs only</label>
          </div>
          <p className="sub">{LISTS[list][1]}</p>
          <DataTable data={loans} columns={loanCols} initialSort={[{ id: 'risk_score', desc: true }]} maxRows={300} />
        </div>
      </Section>
    </div>
  )
}
