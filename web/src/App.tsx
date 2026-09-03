import { NavLink, Outlet } from 'react-router-dom'

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">BDC Loan Quality</div>
        <nav>
          <NavLink to="/" end>Screener</NavLink>
          <NavLink to="/bdcs">BDCs</NavLink>
          <NavLink to="/borrowers">Borrowers</NavLink>
          <NavLink to="/status">Data</NavLink>
        </nav>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
