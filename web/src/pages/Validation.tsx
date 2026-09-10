import { Link } from 'react-router-dom'
import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'
import { num, pct } from '../lib/format'

type V = {
  meta: { base_rate: number; n_loan_quarters: number; fitted_on: string }
  loan_signals: { feature: string; reason: string; n_loan_quarters: number; bad_rate: number; base_rate: number; lift: number; weight: number }[]
  bdc_backtest: { component: string; n_bdc_quarters: number; spearman_fwd_nav: number | null; spearman_fwd_price: number | null; top_minus_bottom_quintile_nav: number | null; weight: number }[]
}

const LABEL: Record<string, string> = {
  pct_debt_below_90: 'Share of debt below 90', pct_debt_below_95: 'Share of debt below 95', wavg_risk: 'Average loan risk score',
  new_deterioration_rate: 'Newly stressed', markdown_share: 'Marked down this quarter', quality_score: 'Quality score',
  pik_share: 'PIK share', new_nonaccrual_rate: 'New non-accruals', nonaccrual_pct_cost: 'Non-accrual share',
  d4_pct_debt_below_90: 'Below 90, 1 yr change', d4_nonaccrual_pct_cost: 'Non-accrual, 1 yr change',
  d1_debt_mark: 'Avg mark, 1 qtr change', generosity: 'Marks vs peers', debt_mark: 'Average mark', nav_chg_4q: 'NAV, 1 yr change',
}
const label = (k: string) => LABEL[k] ?? k.replace(/_/g, ' ')

export default function Validation() {
  const { data, error, loading } = useApi<V>('/api/insights/validation')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  return (
    <div>
      <PageHeader eyebrow="Reference" title="Signal tests" lede="Two checks that sit underneath the strategy: which loan-level warning signs actually precede a loan going bad, and which BDC-level book metrics precede a fall in NAV a year later." />
      <Explain>
        <p><b>How this relates to the strategy.</b> The trading strategy on the <Link to="/results">results page</Link> is tested on stock returns, filing day to filing day. The tests here are the earlier, slower checks: they ask whether a signal predicts credit outcomes (a loan going bad, a BDC's NAV falling) rather than the share price. The loan-level weights feed the risk score shown on every loan; the BDC-level test is why the strategy uses levels (share below 90, average mark) and not one-year changes.</p>
      </Explain>
      <Section title="Loan-level warning signs" meta={`${data.meta.n_loan_quarters.toLocaleString()} loan-quarters · base rate ${pct(data.meta.base_rate)}`}>
        <p className="sub">Fitted on every debt loan-quarter from reconciled filings, 2022 to 2026. A loan is "bad" if within four quarters it goes on non-accrual, is marked below 80, or exits at a loss. Lift is how many times more often loans with the sign went bad than the base rate.</p>
        <div className="panel">
          <div className="tablewrap">
            <table className="grid">
              <thead><tr><th className="l">Warning sign</th><th>Loan-quarters</th><th>Went bad</th><th>Lift vs base</th><th>Weight</th></tr></thead>
              <tbody>
                {data.loan_signals.map((s) => (
                  <tr key={s.feature}><td className="l">{s.reason}</td><td>{s.n_loan_quarters.toLocaleString()}</td><td>{pct(s.bad_rate)}</td><td className={s.lift >= 1.5 ? 'neg' : ''}>{num(s.lift, 2)}x</td><td>{num(s.weight, 2)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note">Weight is the log of the lift, capped at 3. Signs with fewer than 30 observations or no lift get zero weight. A loan's risk score is the probability implied by the base rate plus the weights of the signs present. Signs already inside the definition of "bad" (marked below 80, on non-accrual) have no follow-up sample and so show no lift.</p>
        </div>
      </Section>
      <Section title="BDC-level metrics against the next year's NAV change">
        <p className="sub">For every trusted BDC-quarter, each book metric is ranked against the NAV change over the following four quarters. A negative rank correlation means a higher value of the metric preceded a lower NAV, which is what a warning signal should do.</p>
        <div className="panel">
          <div className="tablewrap">
            <table className="grid">
              <thead><tr><th className="l">Metric</th><th>BDC-quarters</th><th>Rank corr, NAV 1 yr ahead</th><th>Rank corr, price 1 yr ahead</th><th>Top minus bottom fifth, NAV</th><th>Weight</th></tr></thead>
              <tbody>
                {data.bdc_backtest.map((b) => (
                  <tr key={b.component}><td className="l">{label(b.component)}</td><td>{b.n_bdc_quarters}</td><td className={b.spearman_fwd_nav != null && b.spearman_fwd_nav < -0.15 ? 'pos' : ''}>{num(b.spearman_fwd_nav, 3)}</td><td>{num(b.spearman_fwd_price, 3)}</td><td>{pct(b.top_minus_bottom_quintile_nav)}</td><td>{num(b.weight, 3)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note">Weight is max(0, minus the correlation) and is used only by the older validated quality score. Levels (share below 90, average loan risk, newly stressed) predict; one-year changes do not, so they get zero weight. The same pattern holds on stock returns in the <Link to="/results">results</Link>.</p>
        </div>
      </Section>
    </div>
  )
}
