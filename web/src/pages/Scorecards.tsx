import { useState } from 'react'
import { Link } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'
import { cls, pct, signedPct, titleCase } from '../lib/format'
import { G } from '../lib/glossary'

type R = { cik: number; ticker: string | null; name: string; is_public: boolean; latest_period: string; n_evaluated: number; n_bad: number; bad_rate: number | null; late_mark_rate: number | null; n_new_na: number | null; early_warning_rate: number | null; n_loss_exits: number | null; loss_exit_rate: number | null; generosity_4q: number | null; n_shared: number | null }

export default function Scorecards() {
  const { data, error, loading } = useApi<R[]>('/api/insights/scorecards')
  const [publicOnly, setPublicOnly] = useState(true)
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const rows = data.filter((r) => !publicOnly || r.is_public)
  const cols: Col<R>[] = [
    { header: 'Lender', accessorKey: 'name', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.row.original.ticker ?? titleCase(c.getValue<string>())}</Link> },
    { header: 'Public', accessorKey: 'is_public', left: true, cell: (c) => c.getValue<boolean>() ? 'yes' : '' },
    { header: 'Loan-quarters', accessorKey: 'n_evaluated', tip: 'Loan-quarters with a full year of follow-up. Lenders with fewer than 50 are left out.' },
    { header: 'Went bad', id: 'bad_rate', accessorFn: (r) => r.bad_rate ?? -1, tip: G.went_bad, cell: (c) => pct(c.row.original.bad_rate) },
    { header: 'Late marks', id: 'late_mark_rate', accessorFn: (r) => r.late_mark_rate ?? -1, tip: G.late_marks, cell: (c) => { const v = c.row.original.late_mark_rate; return <span className={v != null && v > 0.1 ? 'neg' : ''}>{pct(v)}</span> } },
    { header: 'Early warning', id: 'early_warning_rate', accessorFn: (r) => r.early_warning_rate ?? -1, tip: G.early_warning, cell: (c) => pct(c.row.original.early_warning_rate) },
    { header: 'Loss exits', accessorKey: 'n_loss_exits' },
    { header: 'Loss exit rate', id: 'loss_exit_rate', accessorFn: (r) => r.loss_exit_rate ?? -1, tip: G.loss_exit, cell: (c) => pct(c.row.original.loss_exit_rate) },
    { header: 'Marks vs peers', id: 'generosity_4q', accessorFn: (r) => r.generosity_4q ?? -9, tip: G.generosity, cell: (c) => { const v = c.row.original.generosity_4q; return <span className={cls(v, true)}>{signedPct(v, 2)}</span> } },
    { header: 'Shared borrowers', accessorKey: 'n_shared', tip: 'Borrowers this lender holds that at least one other BDC also holds.' },
  ]
  return (
    <div>
      <PageHeader eyebrow="Explore" title="Lender track records" lede="Whose marks to trust. Each lender is judged on its own history: how often its loans went bad, how often it was still carrying them at par when they did, and whether it marks shared borrowers above or below the other lenders." />
      <Explain>
        <p><b>How to read this.</b> <b>Went bad</b> is the share of debt loan-quarters that went on non-accrual, fell below 80, or left at a loss within four quarters. <b>Late marks</b> is the share of loans carried at 97 or better that went bad within a year: the mark gave no warning. <b>Early warning</b> is the share of loans placed on non-accrual that were already marked below 95 the quarter before. <b>Loss exit rate</b> is the cost of loans that left the book with a last mark below 90, per year, as a share of the average book. <b>Marks vs peers</b> is the average gap to other lenders on the same borrowers over the last four quarters; positive means more generous. A lender with high late marks and generous marks is one whose clean-looking book deserves less credit.</p>
      </Explain>
      <Section title="Track record by lender" meta={`${rows.length} lenders`}>
        <div className="controls">
          <label><input type="checkbox" checked={publicOnly} onChange={(e) => setPublicOnly(e.target.checked)} /> public BDCs only</label>
        </div>
        <div className="panel"><DataTable data={rows} columns={cols} initialSort={[{ id: 'late_mark_rate', desc: true }]} /></div>
      </Section>
    </div>
  )
}
