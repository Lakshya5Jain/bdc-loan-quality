import { useState } from 'react'
import { Link } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { useApi } from '../lib/api'
import { bn, cls, num, pct, signed, titleCase } from '../lib/format'
import { G } from '../lib/glossary'
import { Explain, PageHeader } from '../components/Page'

type Row = {
  cik: number; name: string; ticker: string | null; is_public: boolean; latest_period: string | null
  data_ok: boolean | null; coverage: number | null; n_holdings: number | null; n_debt: number | null
  debt_cost: number | null; debt_mark: number | null; pct_debt_below_90: number | null
  nonaccrual_pct_cost: number | null; pik_share: number | null; new_deterioration_rate: number | null
  quality_score: number | null; quality_trend_4q: number | null; p_nav: number | null; quadrant: string | null
}

export default function BdcList() {
  const [publicOnly, setPublicOnly] = useState(false)
  const [q, setQ] = useState('')
  const { data, error, loading } = useApi<Row[]>(`/api/bdcs?public_only=${publicOnly}`)
  const strat = useApi<{ book: { cik: number; side: string | null; rank: number }[] }>('/api/strategy')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const rows = data.filter((r) => !q || r.name.toLowerCase().includes(q.toLowerCase()) || (r.ticker ?? '').toLowerCase().includes(q.toLowerCase()))
  const side = new Map((strat.data?.book ?? []).map((b) => [b.cik, b]))
  const columns: Col<Row>[] = [
    { header: 'BDC', accessorKey: 'name', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{titleCase(c.getValue<string>())}</Link> },
    { header: 'Strategy', id: 'strategy', accessorFn: (r) => side.get(r.cik)?.rank ?? 999, left: true, tip: G.strategy_side, cell: (c) => { const b = side.get(c.row.original.cik); return b?.side ? <span className={`tag ${b.side}`}>{b.side}</span> : <span className="muted small">{b ? `#${b.rank}` : ''}</span> } },
    { header: 'Ticker', accessorKey: 'ticker', left: true },
    { header: 'Latest', accessorKey: 'latest_period', left: true },
    { header: 'Data check', accessorKey: 'coverage', tip: G.recon, cell: (c) => <span className={c.row.original.data_ok ? '' : 'warn'}>{num(c.getValue<number>(), 2)}</span> },
    { header: 'Holdings', accessorKey: 'n_holdings' },
    { header: 'Debt (cost)', accessorKey: 'debt_cost', cell: (c) => bn(c.getValue<number>()) },
    { header: 'Avg mark', accessorKey: 'debt_mark', tip: G.debt_mark, cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'Loans below 90', accessorKey: 'pct_debt_below_90', tip: G.debt_below_90, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Non-accrual', accessorKey: 'nonaccrual_pct_cost', tip: G.nonaccrual, cell: (c) => pct(c.getValue<number>()) },
    { header: 'PIK share', accessorKey: 'pik_share', tip: G.pik, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Newly stressed', accessorKey: 'new_deterioration_rate', tip: G.new_deterioration, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Book quality', accessorKey: 'quality_score', tip: G.quality, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Trend (1 yr)', accessorKey: 'quality_trend_4q', tip: G.trend, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Price / NAV', accessorKey: 'p_nav', tip: G.p_nav, cell: (c) => num(c.getValue<number>()) },
  ]
  return (
    <div>
      <PageHeader eyebrow="Universe" title="All BDCs" lede="Every BDC in the SEC data, public and private, with its latest trusted quarter. Private BDCs do not trade but lend to the same borrowers, so their marks are a second opinion on the public ones." />
      <Explain><p><b>How to read this.</b> <b>Strategy</b> is the default strategy's current call for liquid public names (long, short, or its rank in the middle). <b>Data check</b> is our loan total divided by the total the BDC reported; near 1.00 is right, and quarters outside 0.90 to 1.10 are excluded from every score. Sort any column by clicking its header.</p></Explain>
      <div className="controls">
        <input placeholder="filter name / ticker" value={q} onChange={(e) => setQ(e.target.value)} />
        <label><input type="checkbox" checked={publicOnly} onChange={(e) => setPublicOnly(e.target.checked)} /> public only</label>
        <span className="muted">{rows.length} BDCs</span>
      </div>
      <div className="panel"><DataTable data={rows} columns={columns} /></div>
    </div>
  )
}
