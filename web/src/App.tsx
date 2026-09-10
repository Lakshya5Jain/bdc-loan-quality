import { NavLink, Outlet } from 'react-router-dom'

const GROUPS: { label: string; items: { to: string; text: string; end?: boolean }[] }[] = [
  { label: 'Read in order', items: [
    { to: '/', text: 'Overview', end: true }, { to: '/data', text: '1 · Data' }, { to: '/methods', text: '2 · Methods and calculations' },
    { to: '/results', text: '3 · Results' }, { to: '/book', text: '4 · Strategy today' },
  ] },
  { label: 'Explore', items: [
    { to: '/screener', text: 'Screener' }, { to: '/bdcs', text: 'BDCs' }, { to: '/borrowers', text: 'Borrowers' },
    { to: '/vintages', text: 'Vintages' }, { to: '/scorecards', text: 'Lender track records' }, { to: '/insights', text: 'Watchlists' },
    { to: '/stale-marks', text: 'Stale marks' }, { to: '/neighbors', text: 'Neighbours of distress' },
  ] },
  { label: 'Reference', items: [{ to: '/validation', text: 'Signal tests' }, { to: '/glossary', text: 'Glossary' }] },
]

export default function App() {
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">BDC Investment Strategy<small>Loan-level credit signals</small></div>
        {GROUPS.map((g) => (
          <nav className="group" key={g.label} aria-label={g.label}>
            <div className="label">{g.label}</div>
            {g.items.map((i) => <NavLink key={i.to} to={i.to} end={i.end}>{i.text}</NavLink>)}
          </nav>
        ))}
        <div className="foot">
          <b>Made by Laksh Jain</b><br /><a href="mailto:ljain@princeton.edu">ljain@princeton.edu</a><br />
          SEC XBRL filings and public prices. Research, not investment advice.
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
