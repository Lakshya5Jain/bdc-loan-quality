import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'
import { bn, num, pct } from '../lib/format'

type Vn = { vintage: number; period_end: string; n_loans: number; cost: number; mark: number | null; pct_stressed: number | null; pct_nonaccrual: number | null }
type D = { latest_period: string; vintages: Vn[] }
type Merged = { label: string; n_loans: number; cost: number; mark: number | null; pct_stressed: number | null; pct_nonaccrual: number | null }

/** Loans first seen before our history starts (late 2022) are one "2022 or earlier" bucket. */
function mergeEarly(rows: Vn[]): Merged[] {
  const early = rows.filter((v) => v.vintage <= 2022)
  const late = rows.filter((v) => v.vintage > 2022)
  const out: Merged[] = []
  if (early.length) {
    const cost = early.reduce((a, v) => a + v.cost, 0)
    const w = (f: (v: Vn) => number | null) => cost ? early.reduce((a, v) => a + (f(v) ?? 0) * v.cost, 0) / cost : null
    out.push({ label: '2022 or earlier', n_loans: early.reduce((a, v) => a + v.n_loans, 0), cost, mark: w((v) => v.mark), pct_stressed: w((v) => v.pct_stressed), pct_nonaccrual: w((v) => v.pct_nonaccrual) })
  }
  return out.concat(late.map((v) => ({ label: String(v.vintage), ...v })))
}

export default function Vintages() {
  const { data, error, loading } = useApi<D>('/api/insights/sectors')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const rows = mergeEarly(data.vintages)
  return (
    <div>
      <PageHeader eyebrow="Explore" title="Vintages" lede="How loans are marked by the year they first appeared in a BDC's schedule. Older loans have had longer to go wrong, so a vintage that is already stressed young is the one to watch." />
      <Explain>
        <p><b>How to read this.</b> A loan's vintage is the first quarter it appeared in any BDC's schedule. Our history starts in late 2022, so everything originated before then sits in one bucket. All figures are across every BDC, public and private, weighted by cost, as of {data.latest_period}. A sector breakdown is not shown: only about one loan in a hundred carries an industry tag in the SEC data, too few to say anything about a sector.</p>
      </Explain>
      <Section title="Debt by year first seen" meta={`as of ${data.latest_period}`}>
        <div className="panel" style={{ maxWidth: 720 }}>
          <div className="tablewrap">
            <table className="grid">
              <thead><tr><th className="l">Vintage</th><th>Loans</th><th>Debt at cost</th><th>Avg mark</th><th>Below 95</th><th>Non-accrual</th></tr></thead>
              <tbody>{rows.map((v) => <tr key={v.label}><td className="l">{v.label}</td><td>{v.n_loans.toLocaleString()}</td><td>{bn(v.cost)}</td><td>{num(v.mark, 3)}</td><td>{pct(v.pct_stressed)}</td><td>{pct(v.pct_nonaccrual)}</td></tr>)}</tbody>
            </table>
          </div>
          <p className="note">Avg mark is fair value over cost. Below 95 and non-accrual are shares of the vintage's debt at cost.</p>
        </div>
      </Section>
    </div>
  )
}
