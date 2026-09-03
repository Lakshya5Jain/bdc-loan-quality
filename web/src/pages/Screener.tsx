import { Link } from 'react-router-dom'
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine, Legend,
} from 'recharts'
import DataTable, { Col } from '../components/DataTable'
import { useApi } from '../lib/api'
import { cls, num, pct, signed, signedPct } from '../lib/format'

export type ScreenRow = {
  cik: number; ticker: string; name: string; signal_period: string; quadrant: string
  quality_score: number | null; quality_trend_4q: number | null; quality_trend_1q: number | null
  pct_debt_below_90: number | null; pct_debt_below_95: number | null; nonaccrual_pct_cost: number | null
  pik_share: number | null; new_deterioration_rate: number | null; new_nonaccrual_rate: number | null
  d4_pct_debt_below_90: number | null; d4_nonaccrual_pct_cost: number | null; d4_pik_share: number | null
  debt_mark: number | null; generosity: number | null; n_shared: number | null
  p_nav: number | null; ret_3m: number | null; ret_6m: number | null; ret_12m: number | null
  div_yield: number | null; nav_chg_4q: number | null; price: number | null; nav_per_share: number | null
  nav_period: string | null; d4_debt_mark: number | null
  short_score: number | null; long_score: number | null; n_debt: number; debt_cost: number | null
  coverage: number | null; data_ok: boolean; n_nonaccrual: number
}

const QCOLOR: Record<string, string> = {
  short_candidate: '#c62828', long_candidate: '#1b7f3b', deteriorating_priced: '#b7791f',
  cheap_but_messy: '#6d28d9', neutral: '#9ca3af',
}

export default function Screener() {
  const { data, error, loading } = useApi<ScreenRow[]>('/api/screen')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading screen…</div>

  const columns: Col<ScreenRow>[] = [
    { header: 'Ticker', accessorKey: 'ticker', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.getValue<string>()}</Link> },
    { header: 'Name', accessorKey: 'name', left: true, cell: (c) => <span className="muted">{c.getValue<string>()}</span> },
    { header: 'Quadrant', accessorKey: 'quadrant', left: true, cell: (c) => <span className={`tag ${c.getValue<string>()}`}>{c.getValue<string>().replace(/_/g, ' ')}</span> },
    { header: 'Quality (z)', accessorKey: 'quality_score', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Trend 4q', accessorKey: 'quality_trend_4q', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Debt <90', accessorKey: 'pct_debt_below_90', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Δ4q <90', accessorKey: 'd4_pct_debt_below_90', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Non-accrual', accessorKey: 'nonaccrual_pct_cost', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Δ4q NA', accessorKey: 'd4_nonaccrual_pct_cost', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>())}</span> },
    { header: 'PIK share', accessorKey: 'pik_share', cell: (c) => pct(c.getValue<number>()) },
    { header: 'New deter.', accessorKey: 'new_deterioration_rate', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Debt mark', accessorKey: 'debt_mark', cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'Generosity', accessorKey: 'generosity', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>(), 2)}</span> },
    { header: 'P/NAV', accessorKey: 'p_nav', cell: (c) => num(c.getValue<number>()) },
    { header: 'Ret 3m', accessorKey: 'ret_3m', cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Ret 6m', accessorKey: 'ret_6m', cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Ret 12m', accessorKey: 'ret_12m', cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'NAV Δ4q', accessorKey: 'nav_chg_4q', cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Div yld', accessorKey: 'div_yield', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Short score', accessorKey: 'short_score', cell: (c) => num(c.getValue<number>()) },
    { header: 'Long score', accessorKey: 'long_score', cell: (c) => num(c.getValue<number>()) },
    { header: 'Period', accessorKey: 'signal_period', cell: (c) => <span className="muted">{c.getValue<string>()}</span> },
  ]
  const scatter = data.filter((d) => d.p_nav != null && d.quality_trend_4q != null)
  return (
    <div>
      <h1>Screener</h1>
      <div className="sub">
        Public BDCs with a current loan-quality score. Quality is a cross-sectional z-score of stressed, non-accrual,
        PIK and newly deteriorating debt (higher = worse); Trend 4q is its change over four quarters. Shorts: worsening
        books at or above median P/NAV whose price has not lagged. Longs: cleaner books trading in the cheapest 30% on P/NAV.
      </div>
      <div className="row">
        <div className="panel">
          <h2>Quality trend vs price / NAV</h2>
          <div className="chart" style={{ height: 320 }}>
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" dataKey="p_nav" name="P/NAV" label={{ value: 'Price / NAV', position: 'bottom', offset: 0 }} domain={['auto', 'auto']} />
                <YAxis type="number" dataKey="quality_trend_4q" name="Trend 4q" label={{ value: 'Quality trend 4q (z, + = worse)', angle: -90, position: 'insideLeft' }} domain={['auto', 'auto']} />
                <ZAxis type="number" dataKey="debt_cost" range={[40, 400]} />
                <ReferenceLine y={0} stroke="#999" />
                <ReferenceLine x={1} stroke="#999" />
                <Legend verticalAlign="top" height={24} />
                <Tooltip cursor={{ strokeDasharray: '3 3' }} content={({ payload }) => {
                  const p = payload?.[0]?.payload as ScreenRow | undefined
                  if (!p) return null
                  return (
                    <div className="panel small">
                      <b>{p.ticker}</b> {p.quadrant.replace(/_/g, ' ')}<br />
                      P/NAV {num(p.p_nav)} · trend {signed(p.quality_trend_4q)} · quality {signed(p.quality_score)}<br />
                      NA {pct(p.nonaccrual_pct_cost)} · &lt;90 {pct(p.pct_debt_below_90)} · 6m {signedPct(p.ret_6m)}
                    </div>
                  )
                }} />
                {Object.keys(QCOLOR).map((qd) => (
                  <Scatter isAnimationActive={false} key={qd} name={qd.replace(/_/g, ' ')} data={scatter.filter((d) => d.quadrant === qd)} fill={QCOLOR[qd]} />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <div className="small muted">Bubble size = debt at cost. Red = short candidate, green = long candidate, amber = deteriorating but already discounted, purple = cheap but messy.</div>
        </div>
      </div>
      <div className="panel">
        <DataTable data={data} columns={columns} initialSort={[{ id: 'short_score', desc: true }]} />
      </div>
    </div>
  )
}
