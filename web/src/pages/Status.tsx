import { useApi } from '../lib/api'
import Stat from '../components/Stat'
import { G } from '../lib/glossary'

type S = {
  files: { source_file: string; loaded_at: string; n_sub: number; n_num: number; n_txt: number }[]
  counts: { holdings: number; loans: number; bdcs: number; screened: number; latest_period: string; latest_price_date: string | null }
  reconciliation: { status: string; n: number }[]
  excluded: { reason: string; n: number }[]
}

export default function Status() {
  const { data, error, loading } = useApi<S>('/api/status')
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  return (
    <div>
      <h1>Data status</h1>
      <div className="sub">Source: SEC BDC XBRL bulk data sets (monthly), yfinance prices. Rebuild with <code>uv run soi ingest --all && uv run soi build all && uv run soi prices && uv run soi build screen</code>.</div>
      <div className="stats">
        <Stat k="BDCs with holdings" v={data.counts.bdcs} />
        <Stat k="Holding-quarters" v={data.counts.holdings.toLocaleString()} />
        <Stat k="Linked loans" v={data.counts.loans.toLocaleString()} />
        <Stat k="Public BDCs screened" v={data.counts.screened} />
        <Stat k="Latest period" v={data.counts.latest_period} />
        <Stat k="Latest price" v={data.counts.latest_price_date ?? '—'} />
      </div>
      <div className="row">
        <div className="panel">
          <h2 title={G.recon}>Data check: our loan totals vs what each BDC reported (BDC-quarters)</h2>
          <table className="grid"><tbody>{data.reconciliation.map((r) => <tr key={r.status}><td className="l">{r.status}</td><td>{r.n}</td></tr>)}</tbody></table>
          <h2>Rows excluded from holdings</h2>
          <table className="grid"><tbody>{data.excluded.map((r) => <tr key={r.reason}><td className="l">{r.reason}</td><td>{r.n.toLocaleString()}</td></tr>)}</tbody></table>
        </div>
        <div className="panel">
          <h2>Loaded SEC bulk files</h2>
          <table className="grid">
            <thead><tr><th className="l">File</th><th className="l">Loaded</th><th>Filings</th><th>Numeric facts</th><th>Text facts</th></tr></thead>
            <tbody>{data.files.map((f) => <tr key={f.source_file}><td className="l">{f.source_file}</td><td className="l">{f.loaded_at.slice(0, 16)}</td><td>{f.n_sub}</td><td>{f.n_num.toLocaleString()}</td><td>{f.n_txt.toLocaleString()}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
