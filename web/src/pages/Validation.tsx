import { useApi } from '../lib/api'
import { num, pct } from '../lib/format'

type V = {
  meta: { base_rate: number; n_loan_quarters: number; fitted_on: string }
  loan_signals: { feature: string; reason: string; n_loan_quarters: number; bad_rate: number; base_rate: number; lift: number; weight: number }[]
  bdc_backtest: { component: string; n_bdc_quarters: number; spearman_fwd_nav: number | null; spearman_fwd_price: number | null; top_minus_bottom_quintile_nav: number | null; weight: number }[]
}

export default function Validation() {
  const { data, error, loading } = useApi<V>('/api/insights/validation')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  return (
    <div>
      <h1>How the signals were validated</h1>
      <div className="sub">Fitted on {data.meta.n_loan_quarters.toLocaleString()} debt loan-quarters (2022 to 2026) from reconciled filings. A loan is "bad" if within four quarters it goes on non-accrual, is marked below 80, or exits at a loss. Base rate: {pct(data.meta.base_rate)}.</div>
      <div className="row">
        <div className="panel">
          <h2>Loan-level signals</h2>
          <table className="grid">
            <thead><tr><th className="l">Signal</th><th>Loan-quarters</th><th>Went bad</th><th>Lift vs base</th><th>Weight</th></tr></thead>
            <tbody>
              {data.loan_signals.map((s) => (
                <tr key={s.feature}><td className="l">{s.reason}</td><td>{s.n_loan_quarters.toLocaleString()}</td><td>{pct(s.bad_rate)}</td><td className={s.lift >= 1.5 ? 'neg' : ''}>{num(s.lift, 2)}x</td><td>{num(s.weight, 2)}</td></tr>
              ))}
            </tbody>
          </table>
          <div className="small muted" style={{ marginTop: 6 }}>Weight = log of the lift, capped at 3. Signals with fewer than 30 observations or no lift get zero weight. The risk score is the implied probability from the base rate plus the weights of the signals present.</div>
        </div>
        <div className="panel">
          <h2>BDC-level signals vs the next year's NAV change</h2>
          <table className="grid">
            <thead><tr><th className="l">Signal</th><th>BDC-quarters</th><th>Rank corr, fwd NAV</th><th>Rank corr, fwd price</th><th>Top − bottom quintile NAV</th><th>Weight</th></tr></thead>
            <tbody>
              {data.bdc_backtest.map((b) => (
                <tr key={b.component}><td className="l">{b.component}</td><td>{b.n_bdc_quarters}</td><td className={b.spearman_fwd_nav != null && b.spearman_fwd_nav < -0.15 ? 'pos' : ''}>{num(b.spearman_fwd_nav, 3)}</td><td>{num(b.spearman_fwd_price, 3)}</td><td>{pct(b.top_minus_bottom_quintile_nav)}</td><td>{num(b.weight, 3)}</td></tr>
              ))}
            </tbody>
          </table>
          <div className="small muted" style={{ marginTop: 6 }}>Negative correlation means a higher signal preceded a lower NAV a year later, which is what a warning signal should do. Weight = max(0, −correlation). Level signals (share of debt below 90, PIK share, non-accruals) predict; four-quarter changes do not, so they get zero weight.</div>
        </div>
      </div>
    </div>
  )
}
