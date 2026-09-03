import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from 'recharts'
import DataTable, { Col } from '../components/DataTable'
import Stat from '../components/Stat'
import { useApi } from '../lib/api'
import { bn, bucketLabel, cls, mm, num, pct, signed, signedPct } from '../lib/format'
import type { ScreenRow } from './Screener'

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
}
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
  const migPeriods = Array.from(new Set(data.migration.map((m) => m.period_end))).sort().slice(-1)
  const mig = data.migration.filter((m) => migPeriods.includes(m.period_end))
  const migCell = (f: string, t: string) => mig.filter((m) => m.from_bucket === f && m.to_bucket === t).reduce((a, m) => a + (m.cost ?? 0), 0)
  const migRowTotal = (f: string) => mig.filter((m) => m.from_bucket === f).reduce((a, m) => a + (m.cost ?? 0), 0)

  const filtered = (loans.data ?? []).filter((l) => !q || l.issuer_name?.toLowerCase().includes(q.toLowerCase()) || l.identifier.toLowerCase().includes(q.toLowerCase()))
  const loanCols: Col<Loan>[] = [
    { header: 'Issuer', accessorKey: 'issuer_name', left: true, cell: (c) => <Link to={`/loans/${c.row.original.loan_id}`}>{c.getValue<string>() || c.row.original.identifier}</Link> },
    { header: 'Type', accessorKey: 'instrument_type', left: true, cell: (c) => `${c.getValue<string>()}${c.row.original.instrument_subtype ? ' · ' + c.row.original.instrument_subtype : ''}` },
    { header: 'Industry', accessorKey: 'industry', left: true, cell: (c) => <span className="muted">{c.getValue<string>() ?? ''}</span> },
    { header: 'Flags', id: 'flags', left: true, cell: (c) => {
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
    { header: 'Mark', accessorKey: 'mark', cell: (c) => <span className={c.getValue<number>() != null && c.getValue<number>() < 0.95 ? 'neg' : ''}>{num(c.getValue<number>(), 3)}</span> },
    { header: 'Δ mark', accessorKey: 'mark_chg', cell: (c) => <span className={cls(c.getValue<number>())}>{signed(c.getValue<number>(), 3)}</span> },
    { header: 'Rate', accessorKey: 'rate', cell: (c) => pct(c.getValue<number>(), 2) },
    { header: 'Spread', accessorKey: 'spread', cell: (c) => pct(c.getValue<number>(), 2) },
    { header: 'PIK', accessorKey: 'pik_rate', cell: (c) => pct(c.getValue<number>(), 2) },
    { header: 'Maturity', accessorKey: 'maturity', left: true },
    { header: 'Obs', accessorKey: 'obs_n' },
  ]

  return (
    <div>
      <h1>{data.bdc.name} {data.bdc.ticker && <span className="muted">({data.bdc.ticker})</span>}</h1>
      <div className="sub">
        Latest period {latest.period_end} · {latest.n_holdings} holdings, {latest.n_debt} debt · reconciliation {num(latest.coverage, 2)}
        {!latest.data_ok && <span className="warn"> · detail does not reconcile to reported total; treat metrics with care</span>}
        {s && <> · <span className={`tag ${s.quadrant}`}>{s.quadrant.replace(/_/g, ' ')}</span></>}
      </div>
      <div className="stats">
        <Stat k="Debt at cost" v={bn(latest.debt_cost)} />
        <Stat k="Debt mark (FV/cost)" v={num(latest.debt_mark, 3)} d={`Δ4q ${signedPct(s?.d4_debt_mark ?? null, 2)}`} />
        <Stat k="Debt marked <90" v={pct(latest.pct_debt_below_90)} d={`Δ4q ${signedPct(s?.d4_pct_debt_below_90 ?? null)}`} cls={cls(s?.d4_pct_debt_below_90 ?? null, true)} />
        <Stat k="Non-accrual (cost)" v={pct(latest.nonaccrual_pct_cost)} d={`${latest.n_nonaccrual} loans · Δ4q ${signedPct(s?.d4_nonaccrual_pct_cost ?? null)}`} />
        <Stat k="PIK share of debt" v={pct(latest.pik_share)} d={`Δ4q ${signedPct(s?.d4_pik_share ?? null)}`} />
        <Stat k="Newly deteriorated" v={pct(latest.new_deterioration_rate)} d="crossed below 95 this quarter" />
        <Stat k="Quality score" v={signed(latest.quality_score)} d={`trend 4q ${signed(latest.quality_trend_4q)}`} cls={cls(latest.quality_trend_4q, true)} />
        {s && <Stat k="Price / NAV" v={num(s.p_nav)} d={`$${num(s.price)} vs NAV $${num(s.nav_per_share)} (${s.nav_period})`} />}
        {s && <Stat k="Total return 6m / 12m" v={`${signedPct(s.ret_6m)} / ${signedPct(s.ret_12m)}`} d={`div yield ${pct(s.div_yield)}`} />}
        {s && s.generosity != null && <Stat k="Mark vs peers" v={signedPct(s.generosity, 2)} d={`${s.n_shared} shared borrowers`} cls={cls(s.generosity, true)} />}
      </div>

      <div className="row">
        <div className="panel">
          <h2>Stress, non-accrual and PIK (% of debt at cost)</h2>
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
          <h2>Debt mark, quality score and price / NAV</h2>
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

      <div className="row">
        <div className="panel">
          <h2>Quarterly metrics</h2>
          <div className="tablewrap">
            <table className="grid">
              <thead><tr><th className="l">Period</th><th>Recon</th><th>Debt</th><th>Mark</th><th>&lt;95</th><th>&lt;90</th><th>&lt;80</th><th>NA</th><th>PIK</th><th>New&lt;95</th><th>New NA</th><th>Markdowns</th><th>Exit loss</th><th>Spread↑</th><th>Extended</th><th>W.spread</th><th>Quality</th><th>NAV/sh</th><th>P/NAV</th></tr></thead>
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
          <h2>Mark migration {migPeriods[0] ? `(${migPeriods[0]}, $mm of debt at cost)` : ''}</h2>
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
              <h2>Marks vs other lenders on shared borrowers</h2>
              <table className="grid">
                <thead><tr><th className="l">Period</th><th>Shared</th><th>Mark vs peers</th><th>Above</th><th>Below</th><th>Peer NA, not flagged</th></tr></thead>
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

      <div className="panel">
        <h2>Holdings</h2>
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
        <DataTable data={filtered} columns={loanCols} initialSort={[{ id: 'mark', desc: false }]} maxRows={600} />
      </div>
    </div>
  )
}
