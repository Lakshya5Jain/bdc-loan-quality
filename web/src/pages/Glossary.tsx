import { PageHeader, Section } from '../components/Page'
import { G } from '../lib/glossary'

const GROUPS: { title: string; terms: [string, string][] }[] = [
  { title: 'The strategy', terms: [['health', 'Health score'], ['strategy_side', 'Long, short, or no position'], ['recon', 'Data check (reconciliation)'],
    ['liquidity_floor', 'Liquidity floor'], ['borrow_cost', 'Borrow cost'], ['turnover', 'Turnover'], ['residual_score', 'Residual score'], ['conviction', 'Conviction weighting'], ['risk_wf', 'Walk-forward loan score']] },
  { title: 'A loan', terms: [
    ['bdc', 'BDC'], ['fair_value', 'Fair value'], ['cost', 'Cost'], ['principal', 'Principal'], ['mark', 'Mark'],
    ['first_lien', 'First lien, second lien, subordinated, equity'], ['nonaccrual', 'Non-accrual'], ['pik', 'PIK'],
    ['spread', 'Spread'], ['spread_up', 'Spread up'], ['extended', 'Extended'], ['vintage', 'Vintage'],
    ['risk', 'Loan risk score'], ['mark_vs_peers', 'Mark vs peers'],
    ['distress_event', 'Distress event'], ['neighbor', 'Neighbour of distress'], ['neighbor_distance', 'Neighbour distance'],
  ] },
  { title: 'A BDC\'s book', terms: [
    ['debt_below_90', 'Loans below 90'], ['debt_below_95', 'Loans below 95'], ['debt_mark', 'Average mark'],
    ['new_deterioration', 'Newly stressed'], ['migration', 'Mark migration'], ['generosity', 'Marks vs peers (generosity)'],
    ['quality', 'Book quality score'], ['trend', 'Trend, 1 year'], ['validated', 'Validated score'],
    ['late_marks', 'Late marks'], ['early_warning', 'Early warning'], ['went_bad', 'Went bad'], ['loss_exit', 'Loss exit rate'],
    ['mark_gap', 'Mark gap (stale marks)'], ['stale_generosity', 'Generosity on shared borrowers'], ['no_second_opinion', 'No-second-opinion share'],
  ] },
  { title: 'The stock', terms: [['nav', 'NAV'], ['p_nav', 'Price / NAV'], ['ret', 'Total return'], ['div_yield', 'Dividend yield'], ['nav_chg', 'NAV change'],
    ['asset_coverage', 'Asset coverage'], ['coverage_distance', 'Distance to the 150% minimum'], ['forced_seller', 'Forced-seller flag']] },
]

export default function Glossary() {
  return (
    <div>
      <PageHeader eyebrow="Reference" title="Glossary" lede="Every term used on the site, in plain English. Hover any column header on any page for the same definition." />
      {GROUPS.map((g) => (
        <Section key={g.title} title={g.title}>
          <div className="panel">
            <dl className="glossary" style={{ margin: 0 }}>
              {g.terms.map(([k, label]) => <div key={k}><dt>{label}</dt><dd>{G[k]}</dd></div>)}
            </dl>
          </div>
        </Section>
      ))}
    </div>
  )
}
