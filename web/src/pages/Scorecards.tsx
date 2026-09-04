import { Link } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { useApi } from '../lib/api'
import { cls, pct, signedPct } from '../lib/format'

type R = { cik: number; ticker: string | null; name: string; is_public: boolean; latest_period: string; n_evaluated: number; n_bad: number; bad_rate: number | null; late_mark_rate: number | null; n_new_na: number | null; early_warning_rate: number | null; n_loss_exits: number | null; loss_exit_rate: number | null; generosity_4q: number | null; n_shared: number | null }

export default function Scorecards() {
  const { data, error, loading } = useApi<R[]>('/api/insights/scorecards')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const cols: Col<R>[] = [
    { header: 'Lender', accessorKey: 'name', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.row.original.ticker ?? c.getValue<string>()}</Link> },
    { header: 'Public', accessorKey: 'is_public', cell: (c) => c.getValue<boolean>() ? 'yes' : '' },
    { header: 'Loan-quarters', accessorKey: 'n_evaluated' },
    { header: 'Went bad', accessorKey: 'bad_rate', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Late marks', accessorKey: 'late_mark_rate', cell: (c) => <span className={c.getValue<number>() != null && c.getValue<number>() > 0.1 ? 'neg' : ''}>{pct(c.getValue<number>())}</span> },
    { header: 'Early warning', accessorKey: 'early_warning_rate', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Loss exits', accessorKey: 'n_loss_exits' },
    { header: 'Loss exit rate', accessorKey: 'loss_exit_rate', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Marks vs peers', accessorKey: 'generosity_4q', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>(), 2)}</span> },
    { header: 'Shared names', accessorKey: 'n_shared' },
  ]
  return (
    <div>
      <h1>Lender scorecards</h1>
      <div className="sub">
        Whose marks to trust. <b>Went bad</b>: share of debt loan-quarters that went on non-accrual, below 80, or exited at a loss within four quarters. <b>Late marks</b>: of loans carried at 97 or better, the share that went bad within a year, so the mark gave no warning. <b>Early warning</b>: of loans placed on non-accrual, the share already marked below 95 the quarter before. <b>Marks vs peers</b>: average difference to other lenders on the same borrowers over the last four quarters, positive = more generous.
      </div>
      <div className="panel"><DataTable data={data} columns={cols} initialSort={[{ id: 'late_mark_rate', desc: true }]} /></div>
    </div>
  )
}
