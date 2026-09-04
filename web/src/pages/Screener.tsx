import { Link } from 'react-router-dom'
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine, Legend,
} from 'recharts'
import DataTable, { Col } from '../components/DataTable'
import { useApi } from '../lib/api'
import { cls, num, pct, signed, signedPct } from '../lib/format'
import { G } from '../lib/glossary'

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
    { header: 'Verdict', accessorKey: 'quadrant', left: true, tip: G.quadrant, cell: (c) => <span className={`tag ${c.getValue<string>()}`}>{c.getValue<string>().replace(/_/g, ' ')}</span> },
    { header: 'Book quality', accessorKey: 'quality_score', tip: G.quality, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Trend (1 yr)', accessorKey: 'quality_trend_4q', tip: G.trend, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Loans below 90', accessorKey: 'pct_debt_below_90', tip: G.debt_below_90, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Below 90, 1 yr change', accessorKey: 'd4_pct_debt_below_90', tip: 'Change in the share of loans below 90 versus four quarters ago.', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Non-accrual', accessorKey: 'nonaccrual_pct_cost', tip: G.nonaccrual + ' Shown as a share of loans by cost.', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Non-accrual, 1 yr change', accessorKey: 'd4_nonaccrual_pct_cost', tip: 'Change in the non-accrual share versus four quarters ago.', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>())}</span> },
    { header: 'PIK share', accessorKey: 'pik_share', tip: G.pik + ' Shown as a share of loans by cost.', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Newly stressed', accessorKey: 'new_deterioration_rate', tip: G.new_deterioration, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Avg mark', accessorKey: 'debt_mark', tip: G.debt_mark, cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'Marks vs peers', accessorKey: 'generosity', tip: G.generosity, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>(), 2)}</span> },
    { header: 'Price / NAV', accessorKey: 'p_nav', tip: G.p_nav, cell: (c) => num(c.getValue<number>()) },
    { header: 'Return 3m', accessorKey: 'ret_3m', tip: G.ret, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Return 6m', accessorKey: 'ret_6m', tip: G.ret, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Return 12m', accessorKey: 'ret_12m', tip: G.ret, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'NAV, 1 yr change', accessorKey: 'nav_chg_4q', tip: G.nav_chg, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Dividend yield', accessorKey: 'div_yield', tip: G.div_yield, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Short score', accessorKey: 'short_score', tip: G.short_score, cell: (c) => num(c.getValue<number>()) },
    { header: 'Long score', accessorKey: 'long_score', tip: G.long_score, cell: (c) => num(c.getValue<number>()) },
    { header: 'Data as of', accessorKey: 'signal_period', tip: 'The latest quarter this BDC has filed. Prices are current.', cell: (c) => <span className="muted">{c.getValue<string>()}</span> },
  ]
  const scatter = data.filter((d) => d.p_nav != null && d.quality_trend_4q != null)
  return (
    <div>
      <h1>Screener</h1>
      <div className="help">
        <b>How to read this.</b> One row per publicly traded BDC. The left half describes the loan book: how many loans the BDC itself marks as impaired, how many have stopped paying, and whether that is getting worse. The right half is the stock: price versus book value and recent returns. A <b>short</b> case is a worsening book that the stock has not priced yet. A <b>long</b> case is a clean book at a discount. Hover any column header for its definition, or see the <a href="/glossary">glossary</a>. Higher book quality and trend numbers mean <i>worse</i>.
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
