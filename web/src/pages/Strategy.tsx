import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import DataTable, { Col } from '../components/DataTable'
import Stat from '../components/Stat'
import { useApi } from '../lib/api'
import { num, pct, signedPct } from '../lib/format'
import { G } from '../lib/glossary'
import { Explain, PageHeader, Section } from '../components/Page'

type Summary = {
  n_quarters: number; quarters_won: number; mean_spread: number | null; worst_spread: number | null
  best_spread: number | null; spread_tstat: number | null; first_period: string; last_period: string
  n_names_now: number; n_long_now: number; latest_period: string; latest_entry_date: string
}
type Period = {
  period_end: string; n_names: number; n_side: number; long_excess: number; short_excess: number
  spread: number; universe_ret: number; longs: string; shorts: string
}
type BookRow = {
  cik: number; ticker: string; name: string; side: 'long' | 'short' | null; rank: number; n: number
  health: number; period_end: string; filed: string; entry_date: string; close_entry: number | null
  p_nav: number | null; pct_debt_below_90: number | null; pct_debt_below_95: number | null
  debt_mark: number | null; nav_chg_4q: number | null
  pct_debt_below_90_rank: number; pct_debt_below_95_rank: number; debt_mark_rank: number; nav_chg_4q_rank: number
}
type SignalRow = {
  signal: string; n_periods: number; mean_ic: number | null; ic_tstat: number | null; ic_hit_rate: number | null
  mean_spread_dir: number | null; ann_spread_net: number | null; avg_hold_days: number | null
}
type Strategy = { summary: Summary; periods: Period[]; book: BookRow[]; signals: SignalRow[] }

const SIGNAL_LABEL: Record<string, string> = {
  pct_debt_below_90: 'Loans below 90', pct_debt_below_95: 'Loans below 95', debt_mark: 'Average mark',
  nav_chg_4q: 'NAV change, 1 yr', nav_chg_1q: 'NAV change, 1 qtr', new_deterioration_rate: 'Newly stressed',
  d4_pct_debt_below_90: 'Below 90, 1 yr change', p_nav: 'Price / NAV', nonaccrual_pct_cost: 'Non-accrual share',
}
const qlabel = (d: string) => {
  const [y, m] = d.split('-')
  return `Q${Math.ceil(Number(m) / 3)} ${y.slice(2)}`
}

type LabVariant = { variant: string; family: string; floor: number; n_quarters: number; quarters_won: number; mean_gross: number; gross_tstat: number | null; worst_gross: number; mean_net: number; net_tstat: number | null; worst_net: number; quarters_won_net: number; mean_borrow: number; mean_turnover: number | null; mean_names: number; mean_side: number }
type LabPeriod = { variant: string; qtr: string; n_names: number; n_side: number; long_excess: number; short_excess: number; spread_gross: number; trading_cost: number; borrow_cost: number; spread_net: number; turnover: number | null; universe_ret: number; longs: string; shorts: string }
type LabSignal = { signal: string; expected_direction: string; n_periods: number; mean_ic: number; ic_tstat: number | null; ic_hit_rate: number; mean_spread_dir: number | null; spread_tstat: number | null }
type LabBook = BookRow & { dollar_vol?: number; rank_lc?: number; n_lc?: number; side_lc?: 'long' | 'short' | null; generosity?: number | null; generosity_rank?: number | null; health5?: number; rank5?: number; n5?: number; side5?: 'long' | 'short' | null }
type Lab = { variants: LabVariant[]; periods: LabPeriod[]; signals: LabSignal[]; book_largecap: LabBook[]; book_5: LabBook[] }

const VARIANT_LABEL: Record<string, string> = {
  default_4: 'Default (4 inputs, $100k floor, equal weights)',
  floor_1m: 'Same, only names trading $1m+ a day',
  floor_2m: 'Same, only names trading $2m+ a day',
  floor_5m: 'Same, only names trading $5m+ a day',
  residual_pnav: 'Score with the price / NAV part removed',
  conviction: 'Conviction-weighted positions',
  plus_generosity: 'Five inputs: + generosity on shared borrowers',
  plus_risk_wavg: 'Five inputs: + average loan warning score',
  plus_risk_hi: 'Five inputs: + share of loans scored 30+',
  combined_1m: 'Combined: + generosity, $1m floor, conviction weights',
}
const LAB_SIGNAL_LABEL: Record<string, string> = {
  generosity: 'Generosity on shared borrowers',
  wavg_risk_wf: 'Average loan warning score (walk-forward)',
  pct_risk_hi_wf: 'Share of loans scored 30+ (walk-forward)',
}

export function Results() {
  const { data, error, loading } = useApi<Strategy>('/api/strategy')
  const lab = useApi<Lab>('/api/lab')
  const [labVariant, setLabVariant] = useState('default_4')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading strategy…</div>
  const { summary: s, periods, signals } = data
  const labPeriods = (lab.data?.periods ?? []).filter((p) => p.variant === labVariant)
  const v2m = lab.data?.variants.find((v) => v.variant === 'floor_2m')
  const vres = lab.data?.variants.find((v) => v.variant === 'residual_pnav')
  const vgen = lab.data?.variants.find((v) => v.variant === 'plus_generosity')
  const vdef = lab.data?.variants.find((v) => v.variant === 'default_4')

  const periodCols: Col<Period>[] = [
    { header: 'Quarter reported', accessorKey: 'period_end', left: true, cell: (c) => qlabel(c.getValue<string>()) },
    { header: 'Names', accessorKey: 'n_names', tip: 'Liquid public BDCs with a trusted filing that quarter.' },
    { header: 'Per side', accessorKey: 'n_side', tip: 'Names in each of the long and short books (the top and bottom fifth).' },
    { header: 'Long book', accessorKey: 'long_excess', tip: 'Average return of the long book over its holding period, minus the sector.', cell: (c) => <span className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Short book', accessorKey: 'short_excess', tip: 'Average return of the short book over its holding period, minus the sector. Negative is good for a short.', cell: (c) => <span className={c.getValue<number>() <= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Long minus short', accessorKey: 'spread', tip: 'What the long/short book made, before costs.', cell: (c) => <b className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</b> },
    { header: 'Sector', accessorKey: 'universe_ret', tip: 'Equal-weight return of the liquid public BDCs over the same windows, for context. The strategy is measured net of this.', cell: (c) => <span className="muted">{signedPct(c.getValue<number>())}</span> },
    { header: 'Longs', accessorKey: 'longs', left: true, wrap: true, cell: (c) => <span className="small muted">{c.getValue<string>().replace(/,/g, ', ')}</span> },
    { header: 'Shorts', accessorKey: 'shorts', left: true, wrap: true, cell: (c) => <span className="small muted">{c.getValue<string>().replace(/,/g, ', ')}</span> },
  ]
  const sigCols: Col<SignalRow>[] = [
    { header: 'Signal', accessorKey: 'signal', left: true, cell: (c) => SIGNAL_LABEL[c.getValue<string>()] ?? c.getValue<string>() },
    { header: 'Quarters', accessorKey: 'n_periods' },
    { header: 'Quarters ranked correctly', accessorKey: 'ic_hit_rate', tip: 'Share of quarters where the signal ranked the next holding period in the expected direction.', cell: (c) => pct(c.getValue<number>(), 0) },
    { header: 'Rank correlation', accessorKey: 'mean_ic', tip: 'Average Spearman correlation between the signal and the next period\'s excess return, in the expected direction. 0.1 is useful, 0.2 is strong for a cross-section of 40 stocks.', cell: (c) => num(c.getValue<number>(), 3) },
    { header: 't-stat', accessorKey: 'ic_tstat', tip: 'Mean correlation divided by its standard error. Above 3 is hard to get by luck in 15 quarters.', cell: (c) => num(c.getValue<number>(), 1) },
    { header: 'Top minus bottom fifth', accessorKey: 'mean_spread_dir', tip: 'Average excess return of the best fifth minus the worst fifth per holding period, in the expected direction.', cell: (c) => <span className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Annualised, net of 40 bp', accessorKey: 'ann_spread_net', cell: (c) => <span className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>(), 0)}</span> },
  ]

  return (
    <div>
      <PageHeader eyebrow="3 · Results" title="Results" lede="How the default strategy has done every quarter since late 2022, replayed with only the information that was public on each trading day, and how each of its inputs performed on its own." />
      <Explain>
        <p><b>How the score works.</b> Every time a BDC files its quarterly report it is ranked against the other liquid public BDCs on four things: the share of loans marked below 90 and below 95 cents on the dollar, the average mark of the whole book, and how NAV per share has moved over the past year. The four ranks average into one <b>health score</b> from 0 (sickest on every measure) to 1 (healthiest). The healthiest fifth is the <b>long book</b>, the sickest fifth the <b>short book</b>, equal dollars each, held until each name's next report.</p>
        <p><b>How to read the returns.</b> Every figure is measured against the equal-weight return of the sector over the same window, so it shows the spread between good and bad books, not whether BDCs went up. Before trading and borrow costs.</p>
      </Explain>

      <div className="stats">
        <Stat k="Quarters tested" v={`${s.quarters_won} of ${s.n_quarters}`} d={`positive · ${qlabel(s.first_period)} to ${qlabel(s.last_period)}`} cls={s.quarters_won / s.n_quarters >= 0.8 ? 'pos' : ''} />
        <Stat k="Long minus short, per quarter" v={signedPct(s.mean_spread)} d="average, before costs" cls={(s.mean_spread ?? 0) > 0 ? 'pos' : 'neg'} />
        <Stat k="Worst quarter" v={signedPct(s.worst_spread)} d={`best ${signedPct(s.best_spread)}`} cls={(s.worst_spread ?? 0) >= 0 ? 'pos' : 'neg'} />
        <Stat k="t-statistic" v={num(s.spread_tstat, 1)} d="above 3 is hard to get by luck" />
        <Stat k="Book today" v={`${s.n_long_now} / ${s.n_long_now}`} d={`long / short, of ${s.n_names_now} liquid names`} />
        <Stat k="Data as of" v={s.latest_period} d={`prices ${s.latest_entry_date}`} />
      </div>

      <Section title="Long minus short, each quarter" meta={`${periods.length} quarters of reports`}>
        <div className="panel">
          <div className="chart" style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={periods.map((p) => ({ ...p, q: qlabel(p.period_end), v: p.spread * 100 }))} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="q" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} unit="%" />
                <ReferenceLine y={0} stroke="#6b7280" />
                <Tooltip formatter={(v: number) => [`${v.toFixed(1)}%`, 'long minus short']} />
                <Bar dataKey="v" isAnimationActive={false}>
                  {periods.map((p) => <Cell key={p.period_end} fill={p.spread >= 0 ? '#1b7f3b' : '#c62828'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="note">Each bar is one quarter of reports: what an equal-dollar long/short book made over the following holding period (about three months), minus the sector return, before trading and borrow costs.</p>
        </div>
      </Section>

      <Section title="Record by quarter">
        <p className="sub">One row per quarter of reports. "Long book" and "short book" are each side's average return over its holding period, net of the sector; the strategy earns the difference.</p>
        <div className="panel">
          <DataTable data={periods} columns={periodCols} initialSort={[{ id: 'period_end', desc: true }]} />
        </div>
      </Section>

      <Section title="Why these four inputs">
        <p className="sub">Each signal was tested on its own the same way: rank every BDC on the day it files, hold to its next filing, compare the top fifth with the bottom fifth net of the sector. The four in the health score ranked the next period correctly in nearly every quarter. Non-accruals, the number everyone quotes, did not.</p>
        <div className="panel">
          <DataTable data={signals} columns={sigCols} initialSort={[{ id: 'ic_tstat', desc: true }]} />
        </div>
      </Section>

      {lab.data && vdef && (
        <Section title="Variants: after costs, at size, and with more inputs" meta={`${lab.data.variants.length} variants · same universe and method`}>
          <Explain>
            <p><b>What this adds.</b> The default strategy above is before costs and holds anything trading over $100,000 a day. This table reruns it with realistic costs (40 basis points per round trip on each leg, plus stock borrow on the short book at 15% a year for names under $1m a day, 5% up to $5m, 1% above), at higher liquidity floors, with the price / NAV part of the score stripped out, with conviction-weighted positions, and with a fifth input added. Ranks are always computed against every liquid name; a floor only changes which names the book may hold.</p>
            <p><b>What it says.</b>
              {' '}After costs the default keeps {signedPct(vdef.mean_net)} a quarter of its {signedPct(vdef.mean_gross)}: borrow on tiny shorts eats {pct(vdef.mean_borrow)} a quarter.
              {v2m && <> At a $2m floor the book is cheaper to run and made {signedPct(v2m.mean_net)} net, {v2m.quarters_won} of {v2m.n_quarters} quarters positive before costs.</>}
              {vres && <> With price / NAV removed the score still worked in {vres.quarters_won} of {vres.n_quarters} quarters at {signedPct(vres.mean_gross)} gross (t-stat {num(vres.gross_tstat, 1)}), so most of the edge is information the price does not carry, with some value tilt.</>}
              {vgen && <> Adding generosity as a fifth input {vgen.mean_gross >= vdef.mean_gross ? 'raised' : 'lowered'} the average to {signedPct(vgen.mean_gross)} and moved the worst quarter to {signedPct(vgen.worst_gross)}.</>}
              {' '}The loan warning score as a fifth input did not help. None of these variants changes the default strategy; they are shown so the trade-offs are visible.</p>
          </Explain>
          <div className="panel">
            <DataTable data={lab.data.variants} columns={[
              { header: 'Variant', accessorKey: 'variant', left: true, wrap: true, cell: (c) => VARIANT_LABEL[c.getValue<string>()] ?? c.getValue<string>() },
              { header: 'Names / quarter', accessorKey: 'mean_names', tip: 'Average tradable names per quarter under this variant.', cell: (c) => num(c.getValue<number>(), 0) },
              { header: 'Quarters won', id: 'won', accessorFn: (r: LabVariant) => r.quarters_won / r.n_quarters, cell: (c) => `${c.row.original.quarters_won} of ${c.row.original.n_quarters}` },
              { header: 'Gross / quarter', accessorKey: 'mean_gross', tip: 'Long minus short, before costs, average per quarter.', cell: (c) => <b className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</b> },
              { header: 't-stat', accessorKey: 'gross_tstat', cell: (c) => num(c.getValue<number | null>(), 1) },
              { header: 'Worst gross', accessorKey: 'worst_gross', cell: (c) => <span className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</span> },
              { header: 'Borrow / quarter', accessorKey: 'mean_borrow', tip: G.borrow_cost, cell: (c) => pct(c.getValue<number>(), 2) },
              { header: 'Net / quarter', accessorKey: 'mean_net', tip: 'After 80 bp of trading cost per quarter and the borrow cost.', cell: (c) => <b className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</b> },
              { header: 'Worst net', accessorKey: 'worst_net', cell: (c) => <span className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</span> },
              { header: 'Quarters up, net', id: 'won_net', accessorFn: (r: LabVariant) => r.quarters_won_net / r.n_quarters, cell: (c) => `${c.row.original.quarters_won_net} of ${c.row.original.n_quarters}` },
              { header: 'Turnover', accessorKey: 'mean_turnover', tip: G.turnover, cell: (c) => num(c.getValue<number | null>(), 2) },
            ] as Col<LabVariant>[]} />
          </div>
          <div className="controls" style={{ marginTop: 12 }}>
            <label>quarter by quarter for{' '}
              <select value={labVariant} onChange={(e) => setLabVariant(e.target.value)}>
                {lab.data.variants.map((v) => <option key={v.variant} value={v.variant}>{VARIANT_LABEL[v.variant] ?? v.variant}</option>)}
              </select>
            </label>
          </div>
          <div className="panel">
            <DataTable data={labPeriods} columns={[
              { header: 'Quarter', accessorKey: 'qtr', left: true, cell: (c) => qlabel(c.getValue<string>()) },
              { header: 'Names', accessorKey: 'n_names' },
              { header: 'Per side', accessorKey: 'n_side' },
              { header: 'Long book', accessorKey: 'long_excess', cell: (c) => <span className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</span> },
              { header: 'Short book', accessorKey: 'short_excess', cell: (c) => <span className={c.getValue<number>() <= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</span> },
              { header: 'Gross', accessorKey: 'spread_gross', cell: (c) => <b className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</b> },
              { header: 'Borrow', accessorKey: 'borrow_cost', cell: (c) => pct(c.getValue<number>(), 2) },
              { header: 'Net', accessorKey: 'spread_net', cell: (c) => <b className={c.getValue<number>() >= 0 ? 'pos' : 'neg'}>{signedPct(c.getValue<number>())}</b> },
              { header: 'Turnover', accessorKey: 'turnover', tip: G.turnover, cell: (c) => num(c.getValue<number | null>(), 2) },
              { header: 'Longs', accessorKey: 'longs', left: true, wrap: true, cell: (c) => <span className="small muted">{c.getValue<string>().replace(/,/g, ', ')}</span> },
              { header: 'Shorts', accessorKey: 'shorts', left: true, wrap: true, cell: (c) => <span className="small muted">{c.getValue<string>().replace(/,/g, ', ')}</span> },
            ] as Col<LabPeriod>[]} initialSort={[{ id: 'qtr', desc: true }]} />
          </div>
        </Section>
      )}

      {lab.data && lab.data.signals.length > 0 && (
        <Section title="Extra inputs on their own">
          <p className="sub">Tested exactly like the signals above. Generosity comes from the <Link to="/stale-marks">stale marks</Link> work; the two loan-score roll-ups are refitted every quarter on outcomes already known by then ({G.risk_wf.toLowerCase()}).</p>
          <div className="panel">
            <DataTable data={lab.data.signals} columns={[
              { header: 'Signal', accessorKey: 'signal', left: true, cell: (c) => LAB_SIGNAL_LABEL[c.getValue<string>()] ?? c.getValue<string>() },
              { header: 'Quarters', accessorKey: 'n_periods' },
              { header: 'Quarters ranked correctly', accessorKey: 'ic_hit_rate', cell: (c) => pct(c.getValue<number>(), 0) },
              { header: 'Rank correlation', accessorKey: 'mean_ic', cell: (c) => num(c.getValue<number>(), 3) },
              { header: 't-stat', accessorKey: 'ic_tstat', cell: (c) => num(c.getValue<number | null>(), 1) },
              { header: 'Top minus bottom fifth', accessorKey: 'mean_spread_dir', cell: (c) => { const v = c.getValue<number | null>(); return <span className={(v ?? 0) >= 0 ? 'pos' : 'neg'}>{signedPct(v)}</span> } },
            ] as Col<LabSignal>[]} />
          </div>
        </Section>
      )}

      <Section title="What to keep in mind">
      <Explain kind="warn">
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          <li><b>One credit cycle.</b> Fifteen quarters of history, from late 2022. Different markets may behave differently.</li>
          <li><b>Small books.</b> Each side holds about nine stocks, so one blow-up moves a quarter.</li>
          <li><b>Missing names.</b> BDCs bought out or delisted since 2022 are not in the test. Their price history is not freely available.</li>
          <li><b>Costs.</b> Returns are before trading costs and before the cost of borrowing stock to short; the annualised column in the signal table charges 40 basis points per round trip and nothing for borrow.</li>
          <li><b>Not advice.</b> This is a research tool. Every number links back to the loans behind it: click a ticker.</li>
        </ul>
      </Explain>
      </Section>
    </div>
  )
}


export function Book() {
  const { data, error, loading } = useApi<Strategy>('/api/strategy')
  const lab = useApi<Lab>('/api/lab')
  const [view, setView] = useState<'default' | 'largecap' | 'five'>('default')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading strategy…</div>
  const { summary: s } = data
  const book: BookRow[] = view === 'default' || !lab.data ? data.book
    : view === 'largecap' ? lab.data.book_largecap.map((b) => ({ ...b, side: b.side_lc ?? null, rank: b.rank_lc ?? b.rank, n: b.n_lc ?? b.n }))
    : lab.data.book_5.map((b) => ({ ...b, side: b.side5 ?? null, rank: b.rank5 ?? b.rank, n: b.n5 ?? b.n, health: b.health5 ?? b.health }))
  const longs = book.filter((b) => b.side === 'long')
  const shorts = book.filter((b) => b.side === 'short').slice().reverse()
  const middle = book.filter((b) => !b.side)
  const bookCols: Col<BookRow>[] = [
    { header: '#', accessorKey: 'rank', tip: 'Rank on the health score among all liquid public BDCs, 1 = healthiest; the short book holds the bottom of the list.', cell: (c) => <span className="muted">{c.getValue<number>()}</span> },
    { header: 'Ticker', accessorKey: 'ticker', left: true, cell: (c) => <Link to={`/bdcs/${c.row.original.cik}`}>{c.getValue<string>()}</Link> },
    { header: 'Name', accessorKey: 'name', left: true, cell: (c) => <span className="muted">{c.getValue<string>()}</span> },
    { header: 'Health score', accessorKey: 'health', tip: G.health, cell: (c) => <b>{num(c.getValue<number>(), 2)}</b> },
    { header: 'Loans below 90', accessorKey: 'pct_debt_below_90', tip: G.debt_below_90, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Loans below 95', accessorKey: 'pct_debt_below_95', tip: G.debt_below_95, cell: (c) => pct(c.getValue<number>()) },
    { header: 'Avg mark', accessorKey: 'debt_mark', tip: G.debt_mark, cell: (c) => num(c.getValue<number>(), 3) },
    { header: 'NAV, 1 yr change', accessorKey: 'nav_chg_4q', tip: G.nav_chg, cell: (c) => <span className={c.getValue<number>() < 0 ? 'neg' : 'pos'}>{signedPct(c.getValue<number>())}</span> },
    { header: 'Price / NAV', accessorKey: 'p_nav', tip: G.p_nav, cell: (c) => num(c.getValue<number>()) },
    { header: 'Data as of', accessorKey: 'period_end', tip: 'Quarter of the filing these numbers come from. Prices are current.', cell: (c) => <span className="muted">{c.getValue<string>()}</span> },
  ]
  return (
    <div>
      <PageHeader eyebrow="4 · Strategy today" title="The book today" lede={`The positions the default strategy holds right now, from the ${s.latest_period} filings and prices through ${s.latest_entry_date}. Long the healthiest fifth, short the sickest fifth, equal dollars, each held until that name's next report.`} />
      <Explain>
        <p><b>How to read it.</b> The health score averages four peer-ranks from the BDC's own loan schedule: share of loans below 90 and below 95 cents on the dollar, the average mark, and the year's NAV change. 1.0 would be the healthiest book on every measure. Names appear only if they trade at least $100,000 a day; a rule that cannot be traded at the quoted price is not a rule. Click a ticker to see the loans behind its score.</p>
      </Explain>
      <Section title="Positions" meta={`${longs.length} long · ${shorts.length} short · ${middle.length} no position`}>
      <div className="controls">
        <label>book{' '}
          <select value={view} onChange={(e) => setView(e.target.value as 'default' | 'largecap' | 'five')}>
            <option value="default">Default: every name over $100k a day</option>
            <option value="largecap">Large-cap: only names over $5m a day</option>
            <option value="five">Five inputs: default plus generosity on shared borrowers</option>
          </select>
        </label>
        {view !== 'default' && <span className="muted small">{view === 'largecap' ? 'Same health score; the book is the top and bottom fifth of the names that clear the $5m floor. Cheaper to short, fewer names, weaker record (see results).' : 'Adds a fifth peer-rank: how the BDC marks shared borrowers against other lenders (lower is healthier). Tested on the results page; not the default.'}</span>}
      </div>
      <div className="row">
        <div className="panel">
          <h2><span className="tag long">long</span> Healthiest {longs.length}</h2>
          <DataTable data={longs} columns={bookCols} initialSort={[{ id: 'rank', desc: false }]} />
        </div>
      </div>
      <div className="row">
        <div className="panel">
          <h2><span className="tag short">short</span> Sickest {shorts.length}</h2>
          <DataTable data={shorts} columns={bookCols} initialSort={[{ id: 'rank', desc: true }]} />
        </div>
      </div>
      <details className="panel">
        <summary>The middle {middle.length}: no position</summary>
        <DataTable data={middle} columns={bookCols} initialSort={[{ id: 'rank', desc: false }]} />
      </details>
      </Section>

    </div>
  )
}
