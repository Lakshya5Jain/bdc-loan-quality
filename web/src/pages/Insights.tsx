import { useState } from 'react'
import { Link } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { useApi } from '../lib/api'
import { cls, mm, num, pct, signed, signedPct } from '../lib/format'
import { G } from '../lib/glossary'

type Stock = { cik: number; ticker: string; name: string; side: string | null; quadrant: string; quality_score: number | null; quality_trend_4q: number | null; validated_score: number | null; wavg_risk: number | null; pct_debt_below_90: number | null; nonaccrual_pct_cost: number | null; pik_share: number | null; new_deterioration_rate: number | null; generosity: number | null; late_mark_rate: number | null; early_warning_rate: number | null; loss_exit_rate: number | null; p_nav: number | null; ret_6m: number | null; ret_12m: number | null; div_yield: number | null; nav_chg_4q: number | null; signal_period: string; reasons: string | null }
type Loan = { list: string; loan_id: string; cik: number; ticker: string | null; bdc_name: string; is_public: boolean; issuer_name: string; borrower_key: string; industry: string | null; instrument_type: string; period_end: string; cost: number; fair_value: number; mark: number | null; risk_score: number; reasons: string[]; n_bdcs: number | null; peer_avg_mark: number | null; mark_vs_peers: number | null }
type W = { stocks: Stock[]; loans: Loan[] }

const LISTS: Record<string, [string, string]> = {
  deteriorating: ['Deteriorating loans', 'Highest estimated chance of non-accrual or loss within four quarters, not yet on non-accrual.'],
  marked_above_peers: ['Marked above other lenders', 'Same borrower held by 2+ BDCs; this lender marks it 5+ points higher. Candidates for a markdown, or for buying from the low marker.'],
  marked_below_peers: ['Marked below other lenders', 'This lender is the most conservative on the name. Either an early warning or a cheap mark.'],
  single_lender_rich: ['Single lender, marked at par, elevated risk', 'No second opinion on the mark and the model sees stress signals.'],
}

export default function Insights() {
  const { data, error, loading } = useApi<W>('/api/insights/watchlists')
  const [list, setList] = useState('deteriorating')
  const [publicOnly, setPublicOnly] = useState(true)
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const sides = data.stocks.filter((s) => s.side)
  const stockCols: Col<Stock>[] = [
    { header: 'Side', accessorKey: 'side', left: true, cell: (c) => <span className={`tag ${c.getValue<string>() === 'short' ? 'short_candidate' : 'long_candidate'}`}>{c.getValue<string>()}</span> },
    { header: 'Ticker', accessorKey: 'ticker', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.getValue<string>()}</Link> },
    { header: 'Book quality (validated)', accessorKey: 'validated_score', tip: G.validated, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Avg loan risk', accessorKey: 'wavg_risk', tip: 'Cost-weighted average of the loan risk score across the book. ' + G.risk, cell: (c) => num(c.getValue<number>(), 0) },
    { header: 'Loans below 90', accessorKey: 'pct_debt_below_90', tip: G.debt_below_90, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Non-accrual', accessorKey: 'nonaccrual_pct_cost', tip: G.nonaccrual, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Price / NAV', accessorKey: 'p_nav', tip: G.p_nav, cell: (c) => num(c.getValue<number>()) },
    { header: 'Return 6m', accessorKey: 'ret_6m', tip: G.ret, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Late marks', accessorKey: 'late_mark_rate', tip: G.late_marks, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Why', accessorKey: 'reasons', left: true, wrap: true },
  ]
  const loans = data.loans.filter((l) => l.list === list && (!publicOnly || l.is_public))
  const loanCols: Col<Loan>[] = [
    { header: 'Borrower', accessorKey: 'issuer_name', left: true, cell: (c) => <Link to={`/borrowers/${encodeURIComponent(c.row.original.borrower_key)}`}>{c.getValue<string>()}</Link> },
    { header: 'Lender', accessorKey: 'ticker', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.getValue<string>() ?? c.row.original.bdc_name}</Link> },
    { header: 'Instrument', accessorKey: 'instrument_type', left: true, cell: (c) => <Link to={`/loans/${c.row.original.loan_id}`}>{c.getValue<string>()}</Link> },
    { header: 'Industry', accessorKey: 'industry', left: true, cell: (c) => <span className="muted">{c.getValue<string>() ?? ''}</span> },
    { header: 'Cost $mm', accessorKey: 'cost', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Mark', accessorKey: 'mark', tip: G.mark, cell: (c) => <span className={c.getValue<number>() != null && c.getValue<number>() < 0.95 ? 'neg' : ''}>{num(c.getValue<number>(), 3)}</span> },
    { header: 'Other lenders', accessorKey: 'n_bdcs', tip: 'Number of BDCs holding this borrower.' },
    { header: 'Peers\' avg mark', accessorKey: 'peer_avg_mark', tip: 'Average mark across all BDCs holding this borrower.', cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'Risk score', accessorKey: 'risk_score', tip: G.risk, cell: (c) => <b className={c.getValue<number>() >= 40 ? 'neg' : ''}>{c.getValue<number>()}</b> },
    { header: 'Why', accessorKey: 'reasons', left: true, wrap: true, cell: (c) => (c.getValue<string[]>() ?? []).join('; ') },
  ]
  return (
    <div>
      <h1>Watchlists</h1>
      <div className="help">
        <b>How to read this.</b> Two lists, one for each audience. <b>Stocks</b>: BDCs where the loan book and the share price disagree. <b>Loans</b>: individual loans worth a look, chosen by the risk model or by disagreement between lenders. Every row says why.
      </div>
      <div className="sub">
        Loan risk is the model's estimated probability (0 to 100) that a loan goes on non-accrual, is marked below 80, or exits at a loss within four quarters, fitted on this database's own history. The stock score weights BDC-level signals by how well they predicted NAV declines over the following year. See <Link to="/validation">how the signals were validated</Link>.
      </div>
      <div className="panel">
        <h2>Stocks</h2>
        {sides.length === 0 ? <div className="muted">No BDC currently meets the long or short criteria.</div> : <DataTable data={sides} columns={stockCols} />}
        <div className="small muted" style={{ marginTop: 6 }}>Everything else on the <Link to="/">screener</Link>. Validated score: cross-sectional z, higher = worse book. Late marks: share of loans carried at par or better that went bad within a year.</div>
      </div>
      <div className="panel">
        <h2>Loans</h2>
        <div className="controls">
          <select value={list} onChange={(e) => setList(e.target.value)}>
            {Object.entries(LISTS).map(([k, [label]]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <label><input type="checkbox" checked={publicOnly} onChange={(e) => setPublicOnly(e.target.checked)} /> public BDCs only</label>
          <span className="muted">{loans.length} loans</span>
        </div>
        <div className="sub">{LISTS[list][1]}</div>
        <DataTable data={loans} columns={loanCols} initialSort={[{ id: 'risk_score', desc: true }]} maxRows={300} />
      </div>
    </div>
  )
}
