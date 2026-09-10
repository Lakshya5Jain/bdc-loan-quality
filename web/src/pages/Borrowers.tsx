import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Explain, PageHeader, Section } from '../components/Page'
import { useApi } from '../lib/api'

type Row = { borrower_key: string; issuer_name: string; n_bdcs: number; n_loans: number; last_period: string }

export default function Borrowers() {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const { data, error, loading } = useApi<Row[]>(`/api/borrowers?q=${encodeURIComponent(term)}&limit=${term ? 50 : 100}`)
  return (
    <div>
      <PageHeader eyebrow="Explore" title="Borrowers" lede="The private companies the BDCs lend to. Many are held by several lenders at once, and each lender marks its slice on its own, so a borrower's page shows where the lenders agree and where they do not." />
      <Explain><p><b>How to read this.</b> Search a company, or start from the list below of the borrowers held by the most BDCs. On a borrower's page, the marks table shows every lender's fair value over cost by quarter; the bottom row is the gap between the highest and lowest mark. A lender far above the others is either better informed or late.</p></Explain>
      <form className="controls" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()) }}>
        <input placeholder="company name, e.g. integrity marketing" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 320 }} />
        <button type="submit">Search</button>
        {term && <button type="button" onClick={() => { setQ(''); setTerm('') }}>Clear</button>}
      </form>
      {error && <div className="err">{error}</div>}
      {loading && <div className="loading">Searching…</div>}
      {data && (
        <Section title={term ? `Matches for "${term}"` : 'Borrowers held by the most BDCs'} meta={`${data.length} borrowers`}>
          <div className="panel">
            <div className="tablewrap">
              <table className="grid">
                <thead><tr><th className="l">Borrower</th><th>Lenders</th><th>Positions</th><th className="l">Last seen</th></tr></thead>
                <tbody>
                  {data.map((r) => (
                    <tr key={r.borrower_key}>
                      <td className="l"><Link to={`/borrowers/${encodeURIComponent(r.borrower_key)}`}>{r.issuer_name}</Link></td>
                      <td>{r.n_bdcs}</td><td>{r.n_loans}</td><td className="l">{r.last_period}</td>
                    </tr>
                  ))}
                  {data.length === 0 && <tr><td className="l muted" colSpan={4}>No borrower matches that name.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </Section>
      )}
    </div>
  )
}
