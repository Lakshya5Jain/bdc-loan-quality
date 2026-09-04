import { G } from '../lib/glossary'

const ORDER: [string, string][] = [
  ['bdc', 'BDC'], ['fair_value', 'Fair value'], ['cost', 'Cost'], ['principal', 'Principal'], ['mark', 'Mark'],
  ['first_lien', 'First lien, second lien, subordinated, equity'], ['nonaccrual', 'Non-accrual'], ['pik', 'PIK'],
  ['spread', 'Spread'], ['spread_up', 'Spread up'], ['extended', 'Extended'], ['migration', 'Mark migration'],
  ['debt_below_95', 'Debt below 95'], ['debt_below_90', 'Debt below 90'], ['new_deterioration', 'Newly deteriorated'],
  ['debt_mark', 'Debt mark'], ['quality', 'Quality score'], ['trend', 'Trend 4q'], ['validated', 'Validated score'],
  ['risk', 'Loan risk score'], ['mark_vs_peers', 'Mark vs peers'], ['generosity', 'Generosity'],
  ['nav', 'NAV'], ['p_nav', 'Price / NAV'], ['ret', 'Total return'], ['div_yield', 'Dividend yield'], ['nav_chg', 'NAV change'],
  ['quadrant', 'Quadrants'], ['short_score', 'Short score'], ['long_score', 'Long score'],
  ['late_marks', 'Late marks'], ['early_warning', 'Early warning'], ['went_bad', 'Went bad'], ['loss_exit', 'Loss exit rate'],
  ['recon', 'Reconciliation'], ['vintage', 'Vintage'],
]

export default function Glossary() {
  return (
    <div>
      <h1>Glossary</h1>
      <div className="sub">Every term used on the site, in plain English. Hover any column header for the same definition.</div>
      <div className="panel">
        <dl className="glossary">
          {ORDER.map(([k, label]) => <div key={k}><dt>{label}</dt><dd>{G[k]}</dd></div>)}
        </dl>
      </div>
    </div>
  )
}
