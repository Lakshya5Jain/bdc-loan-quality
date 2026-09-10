import { Link, useParams } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { useApi } from '../lib/api'
import { cls, mm, num, signed, titleCase } from '../lib/format'
import { Explain, PageHeader, Section } from '../components/Page'

type Loan = { loan_id: string; cik: number; ticker: string | null; bdc_name: string; issuer_name: string; instrument_type: string; is_debt: boolean; first_period: string; last_period: string; n_periods: number; last_fair_value: number | null; last_cost: number | null; last_mark: number | null; min_mark: number | null; ever_nonaccrual: boolean; ever_pik: boolean; exited: boolean; exit_type: string | null }
type Mark = { period_end: string; cik: number; ticker: string | null; bdc_name: string; instrument_type: string; fv: number; cost: number; mark: number | null; nonaccrual: boolean; n_bdcs: number; avg_mark: number | null; mark_vs_peers: number | null }
type Detail = { borrower_key: string; loans: Loan[]; marks: Mark[] }

export default function BorrowerDetail() {
  const { key } = useParams()
  const { data, error, loading } = useApi<Detail>(`/api/borrowers/${encodeURIComponent(key ?? '')}`)
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const name = data.loans[0]?.issuer_name ?? data.borrower_key
  const periods = Array.from(new Set(data.marks.map((m) => m.period_end))).sort()
  const lenders = Array.from(new Set(data.marks.map((m) => m.ticker ?? m.bdc_name)))
  const tickers = new Set(data.marks.map((m) => m.ticker).filter(Boolean))
  const lenderLabel = (l: string) => (tickers.has(l) ? l : titleCase(l))
  const markAt = (lender: string, p: string) => {
    const rows = data.marks.filter((m) => (m.ticker ?? m.bdc_name) === lender && m.period_end === p)
    if (!rows.length) return null
    const cost = rows.reduce((a, r) => a + r.cost, 0)
    const fv = rows.reduce((a, r) => a + r.fv, 0)
    return { mark: cost ? fv / cost : null, na: rows.some((r) => r.nonaccrual) }
  }
  const cols: Col<Loan>[] = [
    { header: 'BDC', accessorKey: 'bdc_name', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.row.original.ticker ?? titleCase(c.getValue<string>())}</Link> },
    { header: 'Instrument', accessorKey: 'instrument_type', left: true, cell: (c) => <Link to={`/loans/${c.row.original.loan_id}`}>{c.getValue<string>().replace(/_/g, ' ')}</Link> },
    { header: 'First', accessorKey: 'first_period', left: true },
    { header: 'Last', accessorKey: 'last_period', left: true, cell: (c) => <>{c.getValue<string>()}{c.row.original.exited && <span className="tag info">{c.row.original.exit_type}</span>}</> },
    { header: 'Qtrs', accessorKey: 'n_periods' },
    { header: 'FV $mm', accessorKey: 'last_fair_value', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Cost $mm', accessorKey: 'last_cost', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Last mark', accessorKey: 'last_mark', cell: (c) => <span className={c.getValue<number>() != null && c.getValue<number>() < 0.95 ? 'neg' : ''}>{num(c.getValue<number>(), 3)}</span> },
    { header: 'Min mark', accessorKey: 'min_mark', cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'NA ever', accessorKey: 'ever_nonaccrual', cell: (c) => c.getValue<boolean>() ? <span className="tag flag">yes</span> : '' },
    { header: 'PIK ever', accessorKey: 'ever_pik', cell: (c) => c.getValue<boolean>() ? <span className="tag info">yes</span> : '' },
  ]
  return (
    <div>
      <PageHeader eyebrow="Borrower" title={name} lede={`${data.loans.length} positions across ${new Set(data.loans.map((l) => l.cik)).size} BDCs. Each lender values its own slice of this company independently, so the table below is a check on every one of them.`} />
      {periods.length > 0 && (
        <Section title="How each lender marks this borrower, by quarter">
        <Explain kind="quiet"><p><b>How to read this.</b> Each cell is fair value over cost for that lender's position in the quarter. Lenders holding the same company should mark it similarly; a lender far above the others is either better informed or late. The bottom row is the gap between the highest and lowest mark. "NA" marks a position on non-accrual.</p></Explain>
        <div className="panel">
          <div className="tablewrap">
            <table className="grid">
              <thead><tr><th className="l">Lender</th>{periods.slice(-10).map((p) => <th key={p}>{p.slice(0, 7)}</th>)}</tr></thead>
              <tbody>
                {lenders.map((l) => (
                  <tr key={l}><td className="l">{lenderLabel(l)}</td>
                    {periods.slice(-10).map((p) => { const m = markAt(l, p); return <td key={p} className={m?.mark != null && m.mark < 0.95 ? 'neg' : ''}>{m ? `${num(m.mark, 3)}${m.na ? ' NA' : ''}` : ''}</td> })}
                  </tr>
                ))}
                <tr><td className="l muted">dispersion (max − min)</td>
                  {periods.slice(-10).map((p) => { const ms = lenders.map((l) => markAt(l, p)?.mark).filter((x): x is number => x != null); return <td key={p} className={cls(ms.length ? -(Math.max(...ms) - Math.min(...ms)) : null)}>{ms.length > 1 ? signed(Math.max(...ms) - Math.min(...ms), 3) : ''}</td> })}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        </Section>
      )}
      <Section title="Every position" meta={`${data.loans.length} positions`}>
        <div className="panel"><DataTable data={data.loans} columns={cols} initialSort={[{ id: 'last_cost', desc: true }]} /></div>
      </Section>
    </div>
  )
}
