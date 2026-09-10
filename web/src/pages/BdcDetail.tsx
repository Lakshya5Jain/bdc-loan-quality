import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from 'recharts'
import DataTable, { Col } from '../components/DataTable'
import Stat from '../components/Stat'
import { useApi } from '../lib/api'
import { bn, bucketLabel, cls, mm, num, pct, signed, signedPct, titleCase } from '../lib/format'
import type { ScreenRow } from './Screener'
import { G } from '../lib/glossary'
import { Explain, PageHeader, Section } from '../components/Page'

type Quarter = {
  period_end: string; data_ok: boolean; coverage: number | null; n_holdings: number; n_debt: number
  debt_cost: number | null; debt_fv: number | null; debt_mark: number | null
  pct_debt_below_95: number | null; pct_debt_below_90: number | null; pct_debt_below_80: number | null
  nonaccrual_pct_cost: number | null; n_nonaccrual: number; pik_share: number | null
  new_deterioration_rate: number | null; new_nonaccrual_rate: number | null; n_new_nonaccrual: number
  markdown_share: number | null; markup_share: number | null; exit_loss_rate: number | null
  spread_up_share: number | null; extended_share: number | null; converted_share: number | null
  wavg_spread: number | null; wavg_rate: number | null; quality_score: number | null
  quality_trend_4q: number | null; nav_per_share: number | null; p_nav: number | null
  price_at_period_end: number | null; new_debt_cost: number | null
}
type Migration = { period_end: string; from_bucket: string; to_bucket: string; n: number; cost: number | null }
type Detail = {
  bdc: { cik: number; name: string; ticker: string | null; is_public: boolean; file_no: string | null }
  quarters: Quarter[]; migration: Migration[]; screen: ScreenRow | null
  generosity: { period_end: string; n_shared: number; generosity: number | null; n_marked_above: number; n_marked_below: number; n_peer_nonaccrual_not_flagged: number }[]
  forced_seller?: Forced[]
  forced_seller_test?: ForcedTest[]
}
type Forced = { period_end: string; coverage: number | null; coverage_source: string | null; distance_pts: number | null; near_limit: boolean | null; b90_up_2q: boolean; flagged: boolean | null; pct_debt_below_90: number | null; exit_loss_fwd2: number | null; fwd2_observed: boolean }
type ForcedTest = { comparison: string; n_flagged: number; rate_flagged: number; n_unflagged: number; rate_unflagged: number; diff: number; ci_lo: number; ci_hi: number; ratio: number | null; p_diff_positive: number }
type Loan = {
  loan_id: string; identifier: string; issuer_name: string; issuer_norm: string; instrument_type: string
  instrument_subtype: string | null; is_debt: boolean; industry: string | null; fair_value: number | null
  cost: number | null; principal: number | null; mark: number | null; prev_mark: number | null
  mark_chg: number | null; rate: number | null; spread: number | null; pik_rate: number | null
  maturity: string | null; nonaccrual_flag: boolean; new_nonaccrual: boolean; pik_flag: boolean
  new_pik: boolean; spread_up: boolean; maturity_extended: boolean; converted_to_equity: boolean
  is_stressed: boolean; is_new: boolean; obs_n: number; match_method: string; footnote_text: string | null
}

const BUCKETS = ['1_ge98', '2_95_98', '3_90_95', '4_80_90', '5_lt80']

export default function BdcDetail() {
  const { cik } = useParams()
  const { data, error, loading } = useApi<Detail>(`/api/bdcs/${cik}`)
  const strat = useApi<{ book: { cik: number; side: 'long' | 'short' | null; rank: number; n: number; health: number; pct_debt_below_90_rank: number; pct_debt_below_95_rank: number; debt_mark_rank: number; nav_chg_4q_rank: number }[] }>('/api/strategy')
  const [period, setPeriod] = useState<string>('')
  const [flag, setFlag] = useState<string>('debt')
  const [q, setQ] = useState('')
  const latest = data?.quarters[data.quarters.length - 1]
  const activePeriod = period || latest?.period_end || ''
  const loans = useApi<Loan[]>(activePeriod ? `/api/bdcs/${cik}/loans?period=${activePeriod}&flag=${flag}` : null)

  const chartData = useMemo(() => (data?.quarters ?? []).map((qq) => ({
    p: qq.period_end.slice(0, 7),
    below90: qq.pct_debt_below_90 == null ? null : +(qq.pct_debt_below_90 * 100).toFixed(2),
    below95: qq.pct_debt_below_95 == null ? null : +(qq.pct_debt_below_95 * 100).toFixed(2),
    na: qq.nonaccrual_pct_cost == null ? null : +(qq.nonaccrual_pct_cost * 100).toFixed(2),
    pik: qq.pik_share == null ? null : +(qq.pik_share * 100).toFixed(2),
    newdet: qq.new_deterioration_rate == null ? null : +(qq.new_deterioration_rate * 100).toFixed(2),
    mark: qq.debt_mark == null ? null : +(qq.debt_mark * 100).toFixed(2),
    pnav: qq.p_nav == null ? null : +qq.p_nav.toFixed(3),
    quality: qq.quality_score == null ? null : +qq.quality_score.toFixed(2),
    ok: qq.data_ok,
  })), [data])

  if (error) return <div className="err">{error}</div>
  if (loading || !data || !latest) return <div className="loading">Loading…</div>
  const s = data.screen
  const sc = (data as unknown as { scorecard?: { late_mark_rate: number | null; early_warning_rate: number | null; loss_exit_rate: number | null } }).scorecard
  const naDelta = s?.d4_nonaccrual_pct_cost ?? null
  const b90Delta = s?.d4_pct_debt_below_90 ?? null
  const dir = (v: number | null, up: string, down: string, flat: string) => v == null ? '' : v > 0.005 ? up : v < -0.005 ? down : flat
  const summary = [
    `${data.bdc.name} has $${((latest.debt_cost ?? 0) / 1e9).toFixed(1)}bn of loans at cost across ${latest.n_debt} positions.`,
    `${pct(latest.pct_debt_below_90)} of that is marked below 90 cents on the dollar${dir(b90Delta, ', up from a year ago', ', down from a year ago', ', about the same as a year ago')}.`,
    `${pct(latest.nonaccrual_pct_cost)} is on non-accrual (${latest.n_nonaccrual} loans)${dir(naDelta, ', rising', ', falling', ', flat')}.`,
    latest.quality_score != null ? `Its book quality is ${latest.quality_score > 0.5 ? 'worse than most BDCs' : latest.quality_score < -0.5 ? 'better than most BDCs' : 'about average'}${latest.quality_trend_4q != null ? (latest.quality_trend_4q > 0.25 ? ' and deteriorating' : latest.quality_trend_4q < -0.25 ? ' and improving' : ' and stable') : ''}.` : 'It has no quality score yet (not enough loans, or the data did not reconcile).',
    s && s.p_nav != null ? `The stock trades at ${num(s.p_nav)}x NAV, ${s.p_nav >= 1.05 ? 'a premium' : s.p_nav <= 0.9 ? 'a clear discount' : 'near book value'}, and has returned ${signedPct(s.ret_12m)} over 12 months.` : '',
    sc && sc.late_mark_rate != null ? `Track record: ${pct(sc.late_mark_rate, 0)} of loans it carried at par went bad within a year${sc.early_warning_rate != null ? `, and it had already marked down ${pct(sc.early_warning_rate, 0)} of loans before placing them on non-accrual` : ''}.` : '',
  ].filter(Boolean).join(' ')
  const migPeriods = Array.from(new Set(data.migration.map((m) => m.period_end))).sort().slice(-1)
  const mig = data.migration.filter((m) => migPeriods.includes(m.period_end))
  const migCell = (f: string, t: string) => mig.filter((m) => m.from_bucket === f && m.to_bucket === t).reduce((a, m) => a + (m.cost ?? 0), 0)
  const migRowTotal = (f: string) => mig.filter((m) => m.from_bucket === f).reduce((a, m) => a + (m.cost ?? 0), 0)

  const filtered = (loans.data ?? []).filter((l) => !q || l.issuer_name?.toLowerCase().includes(q.toLowerCase()) || l.identifier.toLowerCase().includes(q.toLowerCase()))
  const loanCols: Col<Loan>[] = [
    { header: 'Issuer', accessorKey: 'issuer_name', left: true, cell: (c) => <Link to={`/loans/${c.row.original.loan_id}`}>{c.getValue<string>() || c.row.original.identifier}</Link> },
    { header: 'Type', accessorKey: 'instrument_type', left: true, tip: G.first_lien, cell: (c) => `${c.getValue<string>()}${c.row.original.instrument_subtype ? ' · ' + c.row.original.instrument_subtype : ''}` },
    { header: 'Warning signs', id: 'flags', left: true, tip: 'Non-accrual, stressed (mark below 95), marked down more than 2 points this quarter, PIK, spread up, maturity extended, converted to equity, new this quarter.', cell: (c) => {
      const l = c.row.original
      return <>
        {l.nonaccrual_flag && <span className="tag flag">{l.new_nonaccrual ? 'NEW non-accrual' : 'non-accrual'}</span>}
        {l.is_stressed && <span className="tag flag">stressed</span>}
        {l.mark_chg != null && l.mark_chg < -0.02 && <span className="tag flag">markdown</span>}
        {l.pik_flag && <span className="tag info">{l.new_pik ? 'NEW PIK' : 'PIK'}</span>}
        {l.spread_up && <span className="tag info">spread↑</span>}
        {l.maturity_extended && <span className="tag info">extended</span>}
        {l.converted_to_equity && <span className="tag flag">→equity</span>}
        {l.is_new && <span className="tag info">new</span>}
      </> } },
    { header: 'FV ($mm)', accessorKey: 'fair_value', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Cost ($mm)', accessorKey: 'cost', cell: (c) => mm(c.getValue<number>()) },
    { header: 'Mark', accessorKey: 'mark', tip: G.mark, cell: (c) => <span className={c.getValue<number>() != null && c.getValue<number>() < 0.95 ? 'neg' : ''}>{num(c.getValue<number>(), 3)}</span> },
    { header: 'Mark change', accessorKey: 'mark_chg', tip: 'Mark this quarter minus last quarter.', cell: (c) => <span className={cls(c.getValue<number>())}>{signed(c.getValue<number>(), 3)}</span> },
    { header: 'Rate', accessorKey: 'rate', cell: (c) => pct(c.getValue<number>(), 2) },
    { header: 'Spread', accessorKey: 'spread', cell: (c) => pct(c.getValue<number>(), 2) },
    { header: 'PIK rate', accessorKey: 'pik_rate', tip: G.pik, cell: (c) => pct(c.getValue<number>(), 2) },
    { header: 'Maturity', accessorKey: 'maturity', left: true },
    { header: 'Quarters held', accessorKey: 'obs_n', tip: 'How many quarters we have seen this loan.' },
  ]

  const st = strat.data?.book.find((b) => b.cik === data.bdc.cik)
  const rankWord = (r: number) => r <= 0.2 ? 'among the healthiest fifth' : r <= 0.4 ? 'better than most' : r <= 0.6 ? 'about the middle' : r <= 0.8 ? 'worse than most' : 'among the sickest fifth'
  return (
    <div>
      <PageHeader eyebrow={data.bdc.is_public ? 'Universe · public BDC' : 'Universe · private BDC'} title={titleCase(data.bdc.name)} ticker={data.bdc.ticker} lede={summary.replace(data.bdc.name, titleCase(data.bdc.name))} />
      <div className="sub">
        Latest filing {latest.period_end} · {latest.n_holdings} holdings, {latest.n_debt} debt positions · data check {num(latest.coverage, 2)}
        {!latest.data_ok && <span className="warn"> · this quarter did not reconcile to the reported total and is excluded from scores</span>}
      </div>
      {st && (
        <Explain>
          <p><b>Default strategy: {st.side ? <span className={`tag ${st.side}`}>{st.side}</span> : 'no position'}</b>, ranked {st.rank} of {st.n} liquid public BDCs on the health score ({num(st.health, 2)}). On the four inputs it is {rankWord(1 - st.pct_debt_below_90_rank)} for loans below 90, {rankWord(1 - st.pct_debt_below_95_rank)} for loans below 95, {rankWord(1 - st.debt_mark_rank)} on average mark, and {rankWord(1 - st.nav_chg_4q_rank)} on the year's NAV change. See <Link to="/methods">methods</Link> for how the score is built and <Link to="/results">results</Link> for how it has done.</p>
        </Explain>
      )}
      {!st && data.bdc.is_public && <Explain kind="quiet"><p><b>Not in the default strategy.</b> Either the latest quarter did not pass the data check, the book has fewer than ten loans, or the stock trades less than $100,000 a day.</p></Explain>}
      <Section title="The book in numbers" meta={`as of ${latest.period_end}`}>
      <div className="stats">
        <Stat k="Debt at cost" v={bn(latest.debt_cost)} />
        <Stat k="Debt mark (FV/cost)" v={num(latest.debt_mark, 3)} d={`Δ4q ${signedPct(s?.d4_debt_mark ?? null, 2)}`} />
        <Stat k="Debt marked <90" v={pct(latest.pct_debt_below_90)} d={`Δ4q ${signedPct(s?.d4_pct_debt_below_90 ?? null)}`} cls={cls(s?.d4_pct_debt_below_90 ?? null, true)} />
        <Stat k="Non-accrual (cost)" v={pct(latest.nonaccrual_pct_cost)} d={`${latest.n_nonaccrual} loans · Δ4q ${signedPct(s?.d4_nonaccrual_pct_cost ?? null)}`} />
        <Stat k="PIK share of debt" v={pct(latest.pik_share)} d={`Δ4q ${signedPct(s?.d4_pik_share ?? null)}`} />
        <Stat k="Newly deteriorated" v={pct(latest.new_deterioration_rate)} d="crossed below 95 this quarter" />
        <Stat k="Quality score" v={signed(latest.quality_score)} d={`trend 4q ${signed(latest.quality_trend_4q)}`} cls={cls(latest.quality_trend_4q, true)} />
        {s && <Stat k="Price / NAV" v={num(s.p_nav)} d={`$${num(s.price)} vs NAV $${num(s.nav_per_share)} (${s.nav_period})`} />}
        {s && <Stat k="Total return 6m" v={signedPct(s.ret_6m)} d={`12m ${signedPct(s.ret_12m)}`} cls={cls(s.ret_6m)} />}
        {s && <Stat k="Dividend yield" v={pct(s.div_yield)} d="trailing 12 months / price" />}
        {s && s.generosity != null && <Stat k="Mark vs peers" v={signedPct(s.generosity, 2)} d={`${s.n_shared} shared borrowers`} cls={cls(s.generosity, true)} />}
      </div>
      <p className="note">All shares are of the debt book at cost. "Δ4q" is the change against the same quarter a year ago. Hover any table header for a definition.</p>
      </Section>

      {data.forced_seller && data.forced_seller.length > 0 && (() => {
        const fs = data.forced_seller
        const last = fs[fs.length - 1]
        const t = (data.forced_seller_test ?? []).find((x) => x.comparison.startsWith('public: flag'))
        const flaggedQ = fs.filter((f) => f.flagged).map((f) => f.period_end)
        return (
          <Section title="Distance to the leverage limit" meta={`as of ${last.period_end}`}>
            <div className="stats">
              <Stat k="Asset coverage" v={last.coverage == null ? 'n/a' : pct(last.coverage, 0)} d={last.coverage_source ? `${last.coverage_source} · minimum 150%` : 'no balance-sheet tags'} cls={last.coverage != null && last.coverage <= 1.7 ? 'neg' : ''} />
              <Stat k="Room above minimum" v={last.distance_pts == null ? 'n/a' : `${num(last.distance_pts, 0)} pts`} d={last.near_limit ? 'within 20 points' : 'more than 20 points'} cls={last.near_limit ? 'neg' : 'pos'} />
              <Stat k="Loans below 90" v={pct(last.pct_debt_below_90)} d={last.b90_up_2q ? 'up two quarters running' : 'not rising two quarters running'} cls={last.b90_up_2q ? 'neg' : ''} />
              <Stat k="Forced-seller flag" v={last.flagged ? 'FLAG' : 'no'} d={flaggedQ.length ? `flagged in ${flaggedQ.length} past quarter${flaggedQ.length > 1 ? 's' : ''}` : 'never flagged'} cls={last.flagged ? 'neg' : ''} />
            </div>
            <Explain kind={last.flagged ? 'warn' : 'quiet'}>
              <p><b>What this means.</b> {G.forced_seller}{t && <> Across all public BDCs, flagged quarters were followed by loans sold at a loss worth {pct(t.rate_flagged)} of the book over the next two quarters, against {pct(t.rate_unflagged)} for the rest ({t.n_flagged} flagged quarters; 95% interval on the difference {signedPct(t.ci_lo)} to {signedPct(t.ci_hi)}).</>} Coverage comes from the filer's own tag where it reports one, otherwise from net assets and debt on the balance sheet.</p>
            </Explain>
            <details className="panel">
              <summary>Quarter by quarter</summary>
              <DataTable data={fs} columns={[
                { header: 'Quarter', accessorKey: 'period_end', left: true },
                { header: 'Asset coverage', accessorKey: 'coverage', cell: (c) => { const v = c.getValue<number | null>(); return v == null ? '' : pct(v, 0) } },
                { header: 'Source', accessorKey: 'coverage_source', left: true },
                { header: 'Room, pts', accessorKey: 'distance_pts', cell: (c) => num(c.getValue<number | null>(), 0) },
                { header: 'Loans below 90', accessorKey: 'pct_debt_below_90', cell: (c) => pct(c.getValue<number | null>()) },
                { header: 'Rising 2q', accessorKey: 'b90_up_2q', cell: (c) => c.getValue<boolean>() ? 'yes' : '' },
                { header: 'Flag', accessorKey: 'flagged', cell: (c) => c.getValue<boolean | null>() ? <span className="tag short">flag</span> : '' },
                { header: 'Loss exits, next 2q', accessorKey: 'exit_loss_fwd2', tip: 'Cost of loans that left the book with a last mark below 0.90 over the following two quarters, as a share of the debt book.', cell: (c) => c.row.original.fwd2_observed ? pct(c.getValue<number | null>()) : <span className="muted small">not yet</span> },
              ] as Col<Forced>[]} initialSort={[{ id: 'period_end', desc: true }]} />
            </details>
          </Section>
        )
      })()}

      <Section title="How the book has moved">
      <div className="row">
        <div className="panel">
          <h2>Share of loans in trouble, by quarter (% of debt at cost)</h2>
          <div className="chart">
            <ResponsiveContainer>
              <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="p" /><YAxis unit="%" /><Tooltip /><Legend />
                <Line isAnimationActive={false} type="monotone" dataKey="below95" name="<95" stroke="#f59e0b" dot={false} />
                <Line isAnimationActive={false} type="monotone" dataKey="below90" name="<90" stroke="#c62828" dot={false} />
                <Line isAnimationActive={false} type="monotone" dataKey="na" name="non-accrual" stroke="#7c3aed" dot={false} />
                <Line isAnimationActive={false} type="monotone" dataKey="pik" name="PIK" stroke="#2563eb" dot={false} />
                <Line isAnimationActive={false} type="monotone" dataKey="newdet" name="newly <95" stroke="#9ca3af" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="panel">
          <h2>Average mark, book quality (higher = worse) and price / NAV</h2>
          <div className="chart">
            <ResponsiveContainer>
              <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="p" />
                <YAxis yAxisId="l" domain={['auto', 'auto']} />
                <YAxis yAxisId="r" orientation="right" domain={['auto', 'auto']} />
                <Tooltip /><Legend />
                <Line isAnimationActive={false} yAxisId="l" type="monotone" dataKey="mark" name="debt mark (%)" stroke="#1b7f3b" dot={false} />
                <Line isAnimationActive={false} yAxisId="r" type="monotone" dataKey="quality" name="quality z" stroke="#c62828" dot={false} />
                <Line isAnimationActive={false} yAxisId="r" type="monotone" dataKey="pnav" name="P/NAV" stroke="#2563eb" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      </Section>

      <Section title="Quarter by quarter">
      <p className="sub">Every trusted quarter of this BDC's book. Read left to right: how big the book is, how it is marked on average, how much of it is impaired at each threshold, what is newly going wrong, and what the stock paid for it at the time.</p>
      <div className="row">
        <div className="panel">
          <h2>Quarterly metrics <span className="small muted">(hover headers for definitions)</span></h2>
          <div className="tablewrap">
            <table className="grid">
              <thead><tr><th className="l">Quarter</th><th className="tip" title={G.recon}>Data check</th><th>Debt at cost</th><th className="tip" title={G.debt_mark}>Avg mark</th><th className="tip" title={G.debt_below_95}>Below 95</th><th className="tip" title={G.debt_below_90}>Below 90</th><th title="Share of loans marked below 80.">Below 80</th><th className="tip" title={G.nonaccrual}>Non-accrual</th><th className="tip" title={G.pik}>PIK</th><th className="tip" title={G.new_deterioration}>Newly below 95</th><th title="Share of loans newly placed on non-accrual this quarter.">New non-accrual</th><th title="Share of loans marked down more than 2 points this quarter.">Marked down</th><th className="tip" title={G.loss_exit}>Exit losses</th><th className="tip" title={G.spread_up}>Spread up</th><th className="tip" title={G.extended}>Extended</th><th title="Cost-weighted average spread over the base rate.">Avg spread</th><th className="tip" title={G.quality}>Book quality</th><th className="tip" title={G.nav}>NAV / share</th><th className="tip" title={G.p_nav}>Price / NAV</th></tr></thead>
              <tbody>
                {[...data.quarters].reverse().map((qq) => (
                  <tr key={qq.period_end}>
                    <td className="l">{qq.period_end}</td>
                    <td className={qq.data_ok ? '' : 'warn'}>{num(qq.coverage, 2)}</td>
                    <td>{bn(qq.debt_cost)}</td><td>{num(qq.debt_mark, 3)}</td>
                    <td>{pct(qq.pct_debt_below_95)}</td><td>{pct(qq.pct_debt_below_90)}</td><td>{pct(qq.pct_debt_below_80)}</td>
                    <td>{pct(qq.nonaccrual_pct_cost)}</td><td>{pct(qq.pik_share)}</td>
                    <td>{pct(qq.new_deterioration_rate)}</td><td>{pct(qq.new_nonaccrual_rate)}</td>
                    <td>{pct(qq.markdown_share)}</td><td>{pct(qq.exit_loss_rate)}</td>
                    <td>{pct(qq.spread_up_share)}</td><td>{pct(qq.extended_share)}</td>
                    <td>{pct(qq.wavg_spread, 2)}</td>
                    <td className={cls(qq.quality_score, true)}>{signed(qq.quality_score)}</td>
                    <td>{num(qq.nav_per_share)}</td><td>{num(qq.p_nav)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="panel" style={{ flex: '0 1 420px' }}>
          <h2 title={G.migration}>Where the loans moved {migPeriods[0] ? `(${migPeriods[0]}, $mm of debt at cost)` : ''}</h2>
          <table className="heat">
            <thead><tr><th>from \ to</th>{BUCKETS.map((b) => <th key={b}>{bucketLabel[b]}</th>)}<th>total</th></tr></thead>
            <tbody>
              {BUCKETS.map((f) => (
                <tr key={f}>
                  <th>{bucketLabel[f]}</th>
                  {BUCKETS.map((t) => {
                    const v = migCell(f, t)
                    const worse = BUCKETS.indexOf(t) > BUCKETS.indexOf(f)
                    const better = BUCKETS.indexOf(t) < BUCKETS.indexOf(f)
                    const bg = v === 0 ? '' : worse ? `rgba(198,40,40,${Math.min(0.15 + v / (migRowTotal(f) || 1), 0.9)})` : better ? 'rgba(27,127,59,.25)' : '#f1f3f6'
                    return <td key={t} style={{ background: bg }}>{v ? mm(v) : ''}</td>
                  })}
                  <td>{mm(migRowTotal(f))}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="small muted" style={{ marginTop: 6 }}>Rows: mark bucket last quarter. Columns: this quarter. Red cells = migration to a worse bucket.</div>
          {data.generosity.length > 0 && (
            <>
              <h2 title={G.generosity}>Marks vs other lenders on the same borrowers</h2>
              <table className="grid">
                <thead><tr><th className="l">Quarter</th><th title="Borrowers this BDC shares with at least one other BDC.">Shared borrowers</th><th className="tip" title={G.generosity}>Marks vs peers</th><th title="Shared loans this BDC marks 3+ points above the others.">Marked above</th><th title="Shared loans this BDC marks 3+ points below the others.">Marked below</th><th title="Loans another lender has on non-accrual but this BDC does not.">Peers say non-accrual, this BDC does not</th></tr></thead>
                <tbody>
                  {[...data.generosity].reverse().slice(0, 6).map((g) => (
                    <tr key={g.period_end}><td className="l">{g.period_end}</td><td>{g.n_shared}</td>
                      <td className={cls(g.generosity, true)}>{signedPct(g.generosity, 2)}</td><td>{g.n_marked_above}</td><td>{g.n_marked_below}</td><td>{g.n_peer_nonaccrual_not_flagged}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>

      </Section>

      <Section title="Every holding" meta={`${filtered.length} rows`}>
      <p className="sub">The loan schedule as the BDC filed it, one row per position, after subtotals and note tables were removed, largest first. Rows with no cost and a small negative fair value are unfunded commitments the BDC marks below par. Warning signs are computed here; click an issuer to follow the loan across quarters and see every other lender's mark on it.</p>
      <div className="panel">
        <div className="controls">
          <select value={activePeriod} onChange={(e) => setPeriod(e.target.value)}>
            {[...data.quarters].reverse().map((qq) => <option key={qq.period_end} value={qq.period_end}>{qq.period_end}</option>)}
          </select>
          <select value={flag} onChange={(e) => setFlag(e.target.value)}>
            <option value="debt">debt only</option>
            <option value="all">all holdings</option>
            <option value="stressed">stressed (&lt;95)</option>
            <option value="nonaccrual">non-accrual</option>
            <option value="new_nonaccrual">new non-accrual</option>
            <option value="markdown">marked down &gt;2pts</option>
            <option value="pik">PIK</option>
            <option value="new">new this quarter</option>
          </select>
          <input placeholder="search issuer" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="muted">{filtered.length} rows{loans.loading ? ' · loading…' : ''}</span>
        </div>
        <DataTable data={filtered} columns={loanCols} initialSort={[{ id: 'cost', desc: true }]} maxRows={600} />
      </div>
      </Section>
    </div>
  )
}
