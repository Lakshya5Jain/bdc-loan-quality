import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../lib/api'

type Row = { borrower_key: string; issuer_name: string; n_bdcs: number; n_loans: number; last_period: string }

export default function Borrowers() {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const { data, error, loading } = useApi<Row[]>(term.length >= 2 ? `/api/borrowers?q=${encodeURIComponent(term)}` : null)
  return (
    <div>
      <h1>Borrowers</h1>
      <div className="sub">Search a portfolio company to see every BDC that lends to it and how each one marks the loan.</div>
      <form className="controls" onSubmit={(e) => { e.preventDefault(); setTerm(q) }}>
        <input placeholder="company name, e.g. integrity marketing" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 320 }} />
        <button type="submit">Search</button>
      </form>
      {error && <div className="err">{error}</div>}
      {loading && <div className="loading">Searching…</div>}
      {data && (
        <div className="panel">
          <table className="grid">
            <thead><tr><th className="l">Borrower</th><th># BDCs</th><th># loans</th><th className="l">Last seen</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.borrower_key}>
                  <td className="l"><Link to={`/borrowers/${encodeURIComponent(r.borrower_key)}`}>{r.issuer_name}</Link> <span className="muted small">{r.borrower_key}</span></td>
                  <td>{r.n_bdcs}</td><td>{r.n_loans}</td><td className="l">{r.last_period}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
