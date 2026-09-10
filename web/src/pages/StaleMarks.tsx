import { useState } from 'react'
import { Link } from 'react-router-dom'
import DataTable, { Col } from '../components/DataTable'
import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'
import { cls, mm, num, pct, signedPct, titleCase } from '../lib/format'
import { G } from '../lib/glossary'

type Summary = { outcome: string; expected_for_generous: string; n_quarters: number; mean_spread: number; spread_tstat: number | null; hit_rate: number; mean_ic: number | null; ic_tstat: number | null; ic_hit_rate: number | null }
type Period = { qtr: string; outcome: string; n: number; k: number; top_mean: number; bottom_mean: number; spread: number; ic: number | null; top_b90_now: number | null; bottom_b90_now: number | null; top_names: string; bottom_names: string }
type Bdc = { cik: number; ticker: string | null; name: string; is_public: boolean; period_end: string; debt_cost: number; n_shared: number; shared_cost_share: number | null; no_second_opinion_share: number | null; own_mark_shared: number | null; peer_mark_shared: number | null; generosity: number | null; generosity_4q: number | null; n_above_5: number; n_below_5: number }
type Borrower = { borrower_key: string; period_end: string; instrument_type: string; issuer_name: string; industry: string | null; n_bdcs: number; n_public: number; total_cost: number; wavg_mark: number; low_mark: number; high_mark: number; gap: number; low_cik: number; low_lender: string; high_cik: number; high_lender: string; any_nonaccrual: boolean; n_nonaccrual: number; lenders: string }
type D = { latest_period: string; summary: Summary[]; periods: Period[]; bdcs: Bdc[]; borrowers: Borrower[] }

const OUTCOME_LABEL: Record<string, string> = {
  nav_chg_fwd1: 'NAV per share, next quarter',
  nav_chg_fwd2: 'NAV per share, two quarters out',
  b90_chg_fwd1: 'Change in share below 90, next quarter',
  b90_chg_fwd2: 'Change in share below 90, two quarters out',
}

/** Plain-English verdict on one outcome row: does the evidence support the stale-mark story? */
function verdict(s: Summary): string {
  const sign = s.expected_for_generous === 'lower' ? -1 : 1
  const t = s.spread_tstat ?? 0
  if (sign * t >= 2) return 'holds'
  if (sign * t <= -2) return 'runs the other way'
  if (sign * t > 1) return 'leans the right way but is not significant'
  if (sign * t < -1) return 'leans the wrong way but is not significant'
  return 'finds nothing'
}

function qlabel(d: string) {
  const [y, m] = d.split('-')
  return `Q${Math.ceil(Number(m) / 3)} ${y}`
}

export default function StaleMarks() {
  const { data, error, loading } = useApi<D>('/api/stale-marks')
  const [publicOnly, setPublicOnly] = useState(true)
  const [minCost, setMinCost] = useState(5)
  const [outcome, setOutcome] = useState('b90_chg_fwd2')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>

  const bdcs = data.bdcs.filter((r) => !publicOnly || r.is_public)
  const borrowers = data.borrowers.filter((r) => r.total_cost >= minCost * 1e6 && (!publicOnly || r.n_public > 0))
  const periods = data.periods.filter((p) => p.outcome === outcome)
  const nav2 = data.summary.find((s) => s.outcome === 'nav_chg_fwd2')
  const b902 = data.summary.find((s) => s.outcome === 'b90_chg_fwd2')

  const sumCols: Col<Summary>[] = [
    { header: 'What happened next', accessorKey: 'outcome', left: true, cell: (c) => OUTCOME_LABEL[c.getValue<string>()] ?? c.getValue<string>() },
    { header: 'If generous marks are stale', accessorKey: 'expected_for_generous', left: true, tip: 'The direction the generous fifth should show if their marks are lagging: lower NAV change, a bigger rise in loans below 90.' },
    { header: 'Quarters', accessorKey: 'n_quarters' },
    { header: 'Generous minus stingy', accessorKey: 'mean_spread', tip: 'Average outcome of the most generous fifth minus the least generous fifth, per quarter.', cell: (c) => signedPct(c.getValue<number>(), 2) },
    { header: 't-stat', accessorKey: 'spread_tstat', tip: 'Mean spread divided by its standard error across quarters. Above about 2 is unlikely to be noise; below 1 is nothing.', cell: (c) => num(c.getValue<number | null>(), 2) },
    { header: 'Hit rate', accessorKey: 'hit_rate', tip: 'Share of quarters where the spread had the expected sign.', cell: (c) => pct(c.getValue<number>(), 0) },
    { header: 'Rank correlation', accessorKey: 'mean_ic', tip: 'Spearman correlation between generosity and the outcome across all names, averaged over quarters.', cell: (c) => num(c.getValue<number | null>(), 3) },
    { header: 'Corr. t-stat', accessorKey: 'ic_tstat', cell: (c) => num(c.getValue<number | null>(), 2) },
  ]
  const perCols: Col<Period>[] = [
    { header: 'Quarter', accessorKey: 'qtr', left: true, cell: (c) => qlabel(c.getValue<string>()) },
    { header: 'Names', accessorKey: 'n' },
    { header: 'Per side', accessorKey: 'k' },
    { header: 'Generous fifth', accessorKey: 'top_mean', cell: (c) => signedPct(c.getValue<number>(), 2) },
    { header: 'Stingy fifth', accessorKey: 'bottom_mean', cell: (c) => signedPct(c.getValue<number>(), 2) },
    { header: 'Spread', accessorKey: 'spread', cell: (c) => <span className={cls(c.getValue<number>(), outcome.startsWith('b90'))}>{signedPct(c.getValue<number>(), 2)}</span> },
    { header: 'Rank corr.', accessorKey: 'ic', cell: (c) => num(c.getValue<number | null>(), 2) },
    { header: 'Below 90 now, generous', accessorKey: 'top_b90_now', tip: 'The generous fifth\'s share of loans below 90 at the start. If this is already much higher than the stingy fifth\'s, part of the later rise is the existing stress continuing, not stale marks catching up.', cell: (c) => pct(c.getValue<number | null>()) },
    { header: 'Below 90 now, stingy', accessorKey: 'bottom_b90_now', cell: (c) => pct(c.getValue<number | null>()) },
    { header: 'Generous', accessorKey: 'top_names', left: true, wrap: true },
    { header: 'Stingy', accessorKey: 'bottom_names', left: true, wrap: true },
  ]
  const bdcCols: Col<Bdc>[] = [
    { header: 'BDC', accessorKey: 'name', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.row.original.ticker ?? titleCase(c.getValue<string>())}</Link> },
    { header: 'Public', accessorKey: 'is_public', left: true, cell: (c) => c.getValue<boolean>() ? 'yes' : '' },
    { header: 'Quarter', accessorKey: 'period_end', left: true },
    { header: 'Shared positions', accessorKey: 'n_shared', tip: 'Borrower and lien-type pairs this BDC holds that at least one other BDC also holds. Generosity on fewer than five is noise.' },
    { header: 'Own mark, shared', id: 'own', accessorFn: (r) => r.own_mark_shared ?? -1, tip: 'Cost-weighted mark on the shared positions.', cell: (c) => num(c.row.original.own_mark_shared, 3) },
    { header: 'Others\' mark', id: 'peer', accessorFn: (r) => r.peer_mark_shared ?? -1, tip: 'What the other lenders mark the same loans, cost-weighted by this BDC\'s positions.', cell: (c) => num(c.row.original.peer_mark_shared, 3) },
    { header: 'Generosity', id: 'generosity', accessorFn: (r) => r.generosity ?? -9, tip: G.stale_generosity, cell: (c) => { const v = c.row.original.generosity; return <span className={cls(v, true)}>{signedPct(v, 2)}</span> } },
    { header: 'Generosity, 4q avg', id: 'g4', accessorFn: (r) => r.generosity_4q ?? -9, tip: 'Average of the last four quarters, steadier than one quarter.', cell: (c) => signedPct(c.row.original.generosity_4q, 2) },
    { header: 'Marked 5+ above', accessorKey: 'n_above_5', tip: 'Shared positions this BDC marks at least five cents above the others.' },
    { header: 'Marked 5+ below', accessorKey: 'n_below_5' },
    { header: 'No second opinion', id: 'nso', accessorFn: (r) => r.no_second_opinion_share ?? -1, tip: G.no_second_opinion, cell: (c) => { const v = c.row.original.no_second_opinion_share; return <span className={v != null && v > 0.8 ? 'neg' : ''}>{pct(v, 0)}</span> } },
    { header: 'Debt book', accessorKey: 'debt_cost', cell: (c) => mm(c.getValue<number>()) },
  ]
  const borCols: Col<Borrower>[] = [
    { header: 'Borrower', accessorKey: 'issuer_name', left: true, cell: (c) => <Link to={`/borrowers/${encodeURIComponent(c.row.original.borrower_key)}`}>{c.getValue<string>()}</Link> },
    { header: 'Industry', accessorKey: 'industry', left: true, cell: (c) => titleCase(c.getValue<string | null>()) },
    { header: 'Lien', accessorKey: 'instrument_type', left: true, cell: (c) => c.getValue<string>().replace('_', ' ') },
    { header: 'Lenders', accessorKey: 'n_bdcs' },
    { header: 'Total cost', accessorKey: 'total_cost', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Lowest mark', accessorKey: 'low_mark', cell: (c) => num(c.getValue<number>(), 2) },
    { header: 'By', accessorKey: 'low_lender', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.low_cik}`}>{titleCase(c.getValue<string>())}</Link> },
    { header: 'Highest mark', accessorKey: 'high_mark', cell: (c) => num(c.getValue<number>(), 2) },
    { header: 'By', id: 'high_lender', accessorKey: 'high_lender', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.high_cik}`}>{titleCase(c.getValue<string>())}</Link> },
    { header: 'Gap', accessorKey: 'gap', tip: G.mark_gap, cell: (c) => <span className={c.getValue<number>() >= 0.1 ? 'neg' : ''}>{num(c.getValue<number>(), 2)}</span> },
    { header: 'Non-accrual', accessorKey: 'n_nonaccrual', tip: 'How many of the lenders have it on non-accrual. One lender flagging it while others carry it near par is the clearest stale mark.' },
    { header: 'Every lender\'s mark', accessorKey: 'lenders', left: true, wrap: true },
  ]

  return (
    <div>
      <PageHeader eyebrow="Explore" title="Stale marks" lede="The same loan, valued by different lenders. When two BDCs hold the same borrower and disagree, at least one of them is wrong, and the more generous one is usually late. This page lists the biggest disagreements, scores every BDC on how it marks shared loans against the other lenders, and tests whether generous marking predicts trouble." />
      <Explain>
        <p><b>How to read this.</b> A <b>gap</b> is the highest lender's mark minus the lowest on the same borrower and lien type in the same quarter. <b>Generosity</b> is a BDC's cost-weighted mark on its shared loans minus what the other lenders mark those same loans: +0.02 means two cents above the crowd. <b>No second opinion</b> is the share of a BDC's debt book in borrowers nobody else holds, where there is no crowd to check against. Positions marked outside 0 to 1.25 are dropped as unit errors in the filing.</p>
      </Explain>

      <Section title="Does generous marking predict trouble?" meta={`${data.summary[0]?.n_quarters ?? 0} quarters · liquid public BDCs with 5+ shared positions`}>
        <Explain kind={b902 && b902.spread_tstat != null && b902.spread_tstat > 2 ? 'info' : 'warn'}>
          <p><b>The result, honestly.</b> Each quarter the public BDCs are sorted on generosity; the most generous fifth is compared with the least generous fifth on what happened over the next one and two quarters.
            {nav2 && <> On <b>NAV per share</b> the stale-mark story {verdict(nav2)}: over two quarters the generous fifth's NAV moved {signedPct(nav2.mean_spread, 2)} relative to the stingy fifth's (t-stat {num(nav2.spread_tstat, 2)}), and the expected sign, a fall, showed up in only {pct(nav2.hit_rate, 0)} of quarters.</>}
            {b902 && <> On <b>loans below 90</b> it {verdict(b902)}: the generous fifth's share below 90 rose {signedPct(b902.mean_spread, 2)} more than the stingy fifth's over two quarters (t-stat {num(b902.spread_tstat, 2)}, expected sign in {pct(b902.hit_rate, 0)} of quarters). The one-quarter version is weaker.</>}
            {' '}Read the per-quarter table with the "below 90 now" columns: the generous group often starts with more stress already, so part of the later rise is existing trouble continuing rather than stale marks catching up. Generosity is not in the health score, and this test does not change any number elsewhere on the site.</p>
        </Explain>
        <div className="panel"><DataTable data={data.summary} columns={sumCols} /></div>
        <div className="controls" style={{ marginTop: 12 }}>
          <label>outcome{' '}
            <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
              {Object.entries(OUTCOME_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>
        <div className="panel"><DataTable data={periods} columns={perCols} initialSort={[{ id: 'qtr', desc: false }]} /></div>
      </Section>

      <Section title="Every BDC on its shared loans" meta={`latest filing per BDC · ${bdcs.length} BDCs`}>
        <div className="controls">
          <label><input type="checkbox" checked={publicOnly} onChange={(e) => setPublicOnly(e.target.checked)} /> public BDCs only</label>
        </div>
        <div className="panel"><DataTable data={bdcs} columns={bdcCols} initialSort={[{ id: 'generosity', desc: true }]} /></div>
      </Section>

      <Section title="Biggest disagreements" meta={`quarter ending ${data.latest_period} · ${borrowers.length} shared borrowers shown`}>
        <div className="controls">
          <label>minimum combined cost{' '}
            <select value={minCost} onChange={(e) => setMinCost(Number(e.target.value))}>
              {[0, 1, 5, 20, 50].map((v) => <option key={v} value={v}>${v}m</option>)}
            </select>
          </label>
          <label><input type="checkbox" checked={publicOnly} onChange={(e) => setPublicOnly(e.target.checked)} /> at least one public lender</label>
        </div>
        <div className="panel"><DataTable data={borrowers} columns={borCols} initialSort={[{ id: 'gap', desc: true }]} /></div>
        <p className="note">Click a borrower to see every lender's mark over time, or a lender to see its book. Definitions: <Link to="/glossary">glossary</Link>; method: <Link to="/methods">methods page</Link>.</p>
      </Section>
    </div>
  )
}
