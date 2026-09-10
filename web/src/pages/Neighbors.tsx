import { useState } from 'react'
import { Link } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'
import { mm, num, pct, signedPct } from '../lib/format'
import { G } from '../lib/glossary'

type T = { group_name: string; n_events: number; n_neighbor_pairs: number; neighbor_hit_rate: number; n_control_pairs: number; control_hit_rate: number; diff: number; ci_lo: number | null; ci_hi: number | null; ratio: number | null }
type W = { event_key: string; event_name: string; event_period: string; event_mark: number; event_nonaccrual: boolean; event_sector: string | null; neighbor_key: string; neighbor_name: string; neighbor_sector: string | null; rank: number; distance: number; same_sector: boolean; neighbor_mark_t: number; mark_now: number; seen_now: string; neighbor_cost: number; neighbor_lenders: number; holders: string | null; n_public_holders: number | null }
type D = { test: T[]; counts: { n_events: number; n_neighbor_pairs: number; sector_known_share: number; latest_period: string }; watchlist: W[] }

export default function Neighbors() {
  const { data, error, loading } = useApi<D>('/api/neighbors')
  const [publicOnly, setPublicOnly] = useState(true)
  const [maxDist, setMaxDist] = useState(1.0)
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const all = data.test.find((t) => t.group_name === 'all events')
  const rows = data.watchlist.filter((w) => w.distance <= maxDist && (!publicOnly || (w.n_public_holders ?? 0) > 0) && w.mark_now >= 0.95)
  const verdict = all ? (all.ci_lo != null && all.ci_lo > 0 ? 'holds' : all.ci_hi != null && all.ci_hi < 0 ? 'runs the other way' : 'is not distinguishable from chance') : ''

  const testCols: Col<T>[] = [
    { header: 'Group', accessorKey: 'group_name', left: true },
    { header: 'Events', accessorKey: 'n_events' },
    { header: 'Neighbour pairs', accessorKey: 'n_neighbor_pairs' },
    { header: 'Neighbours that fell below 95', accessorKey: 'neighbor_hit_rate', tip: 'Share of neighbour pairs where the neighbour was marked below 0.95 within four quarters of the event.', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Control pairs', accessorKey: 'n_control_pairs' },
    { header: 'Controls that fell below 95', accessorKey: 'control_hit_rate', tip: 'The same share for random near-par borrowers with the same lien bucket.', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Difference', accessorKey: 'diff', cell: (c) => <b className={c.getValue<number>() > 0 ? 'neg' : 'pos'}>{signedPct(c.getValue<number>())}</b> },
    { header: '95% interval', id: 'ci', accessorFn: (r) => r.ci_lo ?? 0, cell: (c) => { const r = c.row.original; return r.ci_lo == null ? '' : `${signedPct(r.ci_lo)} to ${signedPct(r.ci_hi)}` } },
    { header: 'Ratio', accessorKey: 'ratio', tip: 'Neighbour hit rate divided by control hit rate. 1.0 means resembling a failed borrower tells you nothing.', cell: (c) => num(c.getValue<number | null>(), 2) },
  ]
  const wCols: Col<W>[] = [
    { header: 'Watch this borrower', accessorKey: 'neighbor_name', left: true, cell: (c) => <Link to={`/borrowers/${encodeURIComponent(c.row.original.neighbor_key)}`}>{c.getValue<string>()}</Link> },
    { header: 'Sector', accessorKey: 'neighbor_sector', left: true },
    { header: 'Resembles', accessorKey: 'event_name', left: true, tip: 'The borrower that went bad; this row\'s borrower looked most like it the quarter before.', cell: (c) => <Link to={`/borrowers/${encodeURIComponent(c.row.original.event_key)}`}>{c.getValue<string>()}</Link> },
    { header: 'Went bad in', accessorKey: 'event_period', left: true, cell: (c) => <span className="muted">{c.getValue<string>()}{c.row.original.event_nonaccrual ? ' · non-accrual' : ` · ${num(c.row.original.event_mark, 2)}`}</span> },
    { header: 'Distance', accessorKey: 'distance', tip: G.neighbor_distance, cell: (c) => num(c.getValue<number>(), 2) },
    { header: 'Same sector', accessorKey: 'same_sector', cell: (c) => c.getValue<boolean>() ? 'yes' : '' },
    { header: 'Mark then', accessorKey: 'neighbor_mark_t', cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'Mark now', accessorKey: 'mark_now', cell: (c) => <span className={c.getValue<number>() < 0.97 ? 'neg' : ''}>{num(c.getValue<number>(), 3)}</span> },
    { header: 'Cost', accessorKey: 'neighbor_cost', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Lenders', accessorKey: 'neighbor_lenders' },
    { header: 'Held by', accessorKey: 'holders', left: true, wrap: true, cell: (c) => <span className="small">{c.getValue<string | null>() ?? ''}</span> },
  ]

  return (
    <div>
      <PageHeader eyebrow="Explore" title="Neighbours of distress" lede="When a borrower goes bad, which clean-looking borrowers most resemble it? Each new distress event is matched to the ten near-par borrowers whose profile is closest to what the failed one looked like the quarter before. This page tests whether those neighbours go bad more often than random clean loans, and lists the current watchlist with who holds each name." />
      <Explain>
        <p><b>How to read this.</b> {G.distress_event} {G.neighbor} Sector is known for only {pct(data.counts.sector_known_share, 0)} of borrower-quarters, because most filers do not tag an industry, so the match is mostly on loan terms and size. Definitions on the <Link to="/methods">methods page</Link>.</p>
      </Explain>

      <Section title="Do the neighbours go bad more often?" meta={`${data.counts.n_events.toLocaleString()} events · ${data.counts.n_neighbor_pairs.toLocaleString()} neighbour pairs`}>
        <Explain kind={all && all.ci_lo != null && all.ci_lo > 0 ? 'info' : 'warn'}>
          <p><b>The result, honestly.</b> {all && <>Neighbours were marked below 0.95 within four quarters {pct(all.neighbor_hit_rate)} of the time, against {pct(all.control_hit_rate)} for random near-par borrowers with the same lien type: a difference of {signedPct(all.diff)} with a 95% interval of {all.ci_lo == null ? '' : `${signedPct(all.ci_lo)} to ${signedPct(all.ci_hi)}`}. Resembling a borrower that just failed {verdict} as a warning sign.</> } The rows by year show whether that is stable. A ratio near 1.0 would mean the profile carries no information beyond "it is a private loan"; a ratio of 2 would mean twice the odds.</p>
        </Explain>
        <div className="panel"><DataTable data={data.test} columns={testCols} /></div>
      </Section>

      <Section title="Watchlist: clean loans that look like recent failures" meta={`events since ${data.counts.latest_period} minus two quarters · ${rows.length} rows shown`}>
        <div className="controls">
          <label>max distance{' '}
            <select value={maxDist} onChange={(e) => setMaxDist(Number(e.target.value))}>
              {[0.5, 0.75, 1.0, 1.5, 3].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label><input type="checkbox" checked={publicOnly} onChange={(e) => setPublicOnly(e.target.checked)} /> held by at least one public BDC</label>
          <span className="muted small">Only neighbours still marked 0.95 or better today are shown.</span>
        </div>
        <div className="panel"><DataTable data={rows} columns={wCols} initialSort={[{ id: 'distance', desc: false }]} /></div>
      </Section>
    </div>
  )
}
