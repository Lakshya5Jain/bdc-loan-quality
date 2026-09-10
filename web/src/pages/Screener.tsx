import { Link } from 'react-router-dom'
import {
  CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine, Legend,
} from 'recharts'
import DataTable, { Col } from '../components/DataTable'
import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'
import { cls, num, pct, signed, signedPct, titleCase } from '../lib/format'
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
  asset_coverage?: number | null; coverage_source?: string | null; coverage_distance_pts?: number | null
  near_limit?: boolean | null; b90_up_2q?: boolean | null; forced_seller_flag?: boolean | null
}
type BookRow = { cik: number; side: 'long' | 'short' | null; rank: number; n: number; health: number }
type Dot = ScreenRow & { health: number; side: string }

const SIDE_COLOR: Record<string, string> = { long: '#2b7a4b', short: '#a83a2c', none: '#9aa39c' }
const SIDE_NAME: Record<string, string> = { long: 'long book', short: 'short book', none: 'no position' }

export default function Screener() {
  const { data, error, loading } = useApi<ScreenRow[]>('/api/screen')
  const strat = useApi<{ book: BookRow[] }>('/api/strategy')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading screen…</div>
  const book = new Map((strat.data?.book ?? []).map((b) => [b.cik, b]))

  const columns: Col<ScreenRow>[] = [
    { header: 'Ticker', accessorKey: 'ticker', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.getValue<string>()}</Link> },
    { header: 'Name', accessorKey: 'name', left: true, cell: (c) => <span className="muted">{titleCase(c.getValue<string>())}</span> },
    { header: 'Strategy', id: 'strategy', accessorFn: (r) => book.get(r.cik)?.rank ?? 999, left: true, tip: G.strategy_side, cell: (c) => { const b = book.get(c.row.original.cik); return b?.side ? <span className={`tag ${b.side}`}>{b.side}</span> : <span className="muted small">{b ? `#${b.rank} of ${b.n}` : 'not ranked'}</span> } },
    { header: 'Health', id: 'health', accessorFn: (r) => book.get(r.cik)?.health ?? -1, tip: G.health, cell: (c) => { const b = book.get(c.row.original.cik); return b ? num(b.health, 2) : '' } },
    { header: 'Loans below 90', accessorKey: 'pct_debt_below_90', tip: G.debt_below_90, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Loans below 95', accessorKey: 'pct_debt_below_95', tip: G.debt_below_95, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Avg mark', accessorKey: 'debt_mark', tip: G.debt_mark, cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'NAV, 1 yr change', accessorKey: 'nav_chg_4q', tip: G.nav_chg, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Below 90, 1 yr change', accessorKey: 'd4_pct_debt_below_90', tip: 'Change in the share of loans below 90 versus four quarters ago.', cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Non-accrual', accessorKey: 'nonaccrual_pct_cost', tip: G.nonaccrual + ' Shown as a share of loans by cost.', cell: (c) => pct(c.getValue<number>()) },
    { header: 'PIK share', accessorKey: 'pik_share', tip: G.pik + ' Shown as a share of loans by cost.', cell: (c) => pct(c.getValue<number>()) },
    { header: 'Newly stressed', accessorKey: 'new_deterioration_rate', tip: G.new_deterioration, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Marks vs peers', accessorKey: 'generosity', tip: G.generosity, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signedPct(c.getValue<number>(), 2)}</span> },
    { header: 'Book quality', accessorKey: 'quality_score', tip: G.quality, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Trend (1 yr)', accessorKey: 'quality_trend_4q', tip: G.trend, cell: (c) => <span className={cls(c.getValue<number>(), true)}>{signed(c.getValue<number>())}</span> },
    { header: 'Price / NAV', accessorKey: 'p_nav', tip: G.p_nav, cell: (c) => num(c.getValue<number>()) },
    { header: 'Asset coverage', id: 'asset_coverage', accessorFn: (r) => r.asset_coverage ?? 99, tip: G.asset_coverage + ' Tagged by the filer where available, otherwise computed.', cell: (c) => { const v = c.row.original.asset_coverage; return v == null ? '' : <span className={v <= 1.7 ? 'neg' : ''}>{pct(v, 0)}</span> } },
    { header: 'Forced seller?', id: 'forced', accessorFn: (r) => (r.forced_seller_flag ? 2 : r.near_limit ? 1 : 0), tip: G.forced_seller, cell: (c) => { const r = c.row.original; return r.forced_seller_flag ? <span className="tag short">flag</span> : r.near_limit ? <span className="muted small">near limit</span> : '' } },
    { header: 'Return 3m', accessorKey: 'ret_3m', tip: G.ret, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Return 12m', accessorKey: 'ret_12m', tip: G.ret, cell: (c) => <span className={cls(c.getValue<number>())}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Dividend yield', accessorKey: 'div_yield', tip: G.div_yield, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Data as of', accessorKey: 'signal_period', tip: 'The latest quarter this BDC has filed. Prices are current.', cell: (c) => <span className="muted">{c.getValue<string>()}</span> },
  ]
  const dots: Dot[] = data.flatMap((d) => {
    const b = book.get(d.cik)
    return b && d.p_nav != null ? [{ ...d, health: b.health, side: b.side ?? 'none' }] : []
  })
  return (
    <div>
      <PageHeader eyebrow="Explore" title="Screener" lede="Every publicly traded BDC on one table: the strategy's current call, the four inputs behind it, the rest of the loan-book metrics, and what the stock pays for it. Sort any column by clicking its header; hover a header for the definition." />
      <Explain>
        <p><b>How to read this.</b> <b>Strategy</b> is the default strategy's call on the day of the BDC's latest filing: long, short, or its rank among the liquid public names. <b>Health</b> is the score behind it, 1 healthiest. The next four columns are the score's inputs. The remaining loan-book columns are for context: higher book quality and trend numbers mean <i>worse</i>. The right-hand columns are the stock. A name is unranked when its latest quarter failed the data check, the book has fewer than ten loans, or the shares trade less than $100,000 a day. See the <Link to="/book">strategy today</Link> for the current book and the <Link to="/methods">methods</Link> for the score.</p>
      </Explain>
      <Section title="Health score against price / NAV">
        <div className="panel">
          <div className="chart" style={{ height: 340 }}>
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" dataKey="p_nav" name="Price / NAV" label={{ value: 'Price / NAV', position: 'bottom', offset: 0 }} domain={['auto', 'auto']} />
                <YAxis type="number" dataKey="health" name="Health" label={{ value: 'Health score (1 = healthiest)', angle: -90, position: 'insideLeft' }} domain={[0, 1]} />
                <ZAxis type="number" dataKey="debt_cost" range={[40, 400]} />
                <ReferenceLine x={1} stroke="#999" />
                <Legend verticalAlign="top" height={24} />
                <Tooltip cursor={{ strokeDasharray: '3 3' }} content={({ payload }) => {
                  const p = payload?.[0]?.payload as Dot | undefined
                  if (!p) return null
                  return (
                    <div className="panel small">
                      <b>{p.ticker}</b> {SIDE_NAME[p.side]}<br />
                      health {num(p.health, 2)} · P/NAV {num(p.p_nav)}<br />
                      below 90 {pct(p.pct_debt_below_90)} · avg mark {num(p.debt_mark, 3)} · NAV 1y {signedPct(p.nav_chg_4q)}
                    </div>
                  )
                }} />
                {['long', 'none', 'short'].map((sd) => (
                  <Scatter isAnimationActive={false} key={sd} name={SIDE_NAME[sd]} data={dots.filter((d) => d.side === sd)} fill={SIDE_COLOR[sd]} />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p className="note">Bubble size is debt at cost. Green is the long book, red the short book. The strategy ignores price / NAV; the chart shows whether the market already agrees with the loan book. A sick book at a premium and a healthy book at a discount are the cases where it does not.</p>
        </div>
      </Section>
      <Section title="All public BDCs" meta={`${data.length} names`}>
        <div className="panel">
          <DataTable data={data} columns={columns} initialSort={[{ id: 'strategy', desc: false }]} />
        </div>
      </Section>
    </div>
  )
}
