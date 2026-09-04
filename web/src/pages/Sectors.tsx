import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from 'recharts'
import { useApi } from '../lib/api'
import { bn, cls, num, pct, signedPct } from '../lib/format'

type S = { industry: string; period_end: string; n_loans: number; n_bdcs: number; cost: number; mark: number | null; pct_stressed: number | null; pct_nonaccrual: number | null; wavg_risk: number | null; d4_pct_stressed?: number | null }
type Vn = { vintage: number; period_end: string; n_loans: number; cost: number; mark: number | null; pct_stressed: number | null; pct_nonaccrual: number | null }
type D = { latest_period: string; sectors: S[]; sector_history: S[]; vintages: Vn[] }
const COLORS = ['#c62828', '#2563eb', '#1b7f3b', '#b7791f', '#7c3aed', '#0e7490', '#9ca3af', '#db2777']

export default function Sectors() {
  const { data, error, loading } = useApi<D>('/api/insights/sectors')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const inds = Array.from(new Set(data.sector_history.map((s) => s.industry))).slice(0, 8)
  const periods = Array.from(new Set(data.sector_history.map((s) => s.period_end))).sort()
  const chart = periods.map((p) => {
    const row: Record<string, unknown> = { p: p.slice(0, 7) }
    for (const i of inds) { const s = data.sector_history.find((x) => x.industry === i && x.period_end === p); row[i] = s?.pct_stressed == null ? null : +(s.pct_stressed * 100).toFixed(1) }
    return row
  })
  return (
    <div>
      <h1>Sectors and vintages</h1>
      <div className="sub">Where stress is concentrated, as of {data.latest_period}. Industry comes from the filer's own tagging where available; loans without an industry tag are grouped as Unknown.</div>
      <div className="panel">
        <h2>Share of debt marked below 95 by industry, largest industries</h2>
        <div className="chart" style={{ height: 280 }}>
          <ResponsiveContainer><LineChart data={chart}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="p" /><YAxis unit="%" /><Tooltip /><Legend />
            {inds.map((i, k) => <Line key={i} type="monotone" dataKey={i} stroke={COLORS[k % COLORS.length]} dot={false} isAnimationActive={false} />)}
          </LineChart></ResponsiveContainer>
        </div>
      </div>
      <div className="row">
        <div className="panel">
          <h2>Industries (debt at cost over $100mm)</h2>
          <div className="tablewrap"><table className="grid">
            <thead><tr><th className="l">Industry</th><th>Loans</th><th>BDCs</th><th>Debt</th><th>Mark</th><th>&lt;95</th><th>Δ4q &lt;95</th><th>Non-accrual</th><th>Avg risk</th></tr></thead>
            <tbody>{data.sectors.map((s) => <tr key={s.industry}><td className="l">{s.industry}</td><td>{s.n_loans}</td><td>{s.n_bdcs}</td><td>{bn(s.cost)}</td><td>{num(s.mark, 3)}</td><td>{pct(s.pct_stressed)}</td><td className={cls(s.d4_pct_stressed, true)}>{signedPct(s.d4_pct_stressed)}</td><td>{pct(s.pct_nonaccrual)}</td><td>{num(s.wavg_risk, 0)}</td></tr>)}</tbody>
          </table></div>
        </div>
        <div className="panel" style={{ flex: '0 1 460px' }}>
          <h2>By origination year (first quarter seen)</h2>
          <table className="grid">
            <thead><tr><th className="l">Vintage</th><th>Loans</th><th>Debt</th><th>Mark</th><th>&lt;95</th><th>Non-accrual</th></tr></thead>
            <tbody>{data.vintages.map((v) => <tr key={v.vintage}><td className="l">{v.vintage}{v.vintage <= 2022 ? ' or earlier' : ''}</td><td>{v.n_loans}</td><td>{bn(v.cost)}</td><td>{num(v.mark, 3)}</td><td>{pct(v.pct_stressed)}</td><td>{pct(v.pct_nonaccrual)}</td></tr>)}</tbody>
          </table>
          <div className="small muted" style={{ marginTop: 6 }}>Our history starts in late 2022, so that vintage also holds everything originated before.</div>
        </div>
      </div>
    </div>
  )
}
