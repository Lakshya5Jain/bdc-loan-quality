import { Link, useParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from 'recharts'
import Stat from '../components/Stat'
import { useApi } from '../lib/api'
import { cls, mm, num, pct, signed, titleCase } from '../lib/format'
import { Explain, PageHeader, Section } from '../components/Page'
import { G } from '../lib/glossary'

type Hist = {
  period_end: string; identifier: string; instrument_type: string; fair_value: number | null; cost: number | null
  principal: number | null; mark: number | null; mark_chg: number | null; rate: number | null; spread: number | null
  floor_rate: number | null; pik_rate: number | null; maturity: string | null; nonaccrual_flag: boolean
  pik_flag: boolean; spread_up: boolean; maturity_extended: boolean; match_method: string; footnote_text: string | null
  pct_net_assets: number | null
}
type Peer = { cik: number; ticker: string | null; name: string; period_end: string; instrument_type: string; fv: number; cost: number; mark: number | null; nonaccrual: boolean; mark_vs_peers: number | null }
type Detail = {
  loan: { loan_id: string; cik: number; bdc_name: string; ticker: string | null; issuer_name: string; borrower_key: string; instrument_type: string; identifier: string; first_period: string; last_period: string; n_periods: number; exited: boolean; exit_type: string | null; last_mark: number | null; min_mark: number | null; ever_nonaccrual: boolean; industry: string | null }
  history: Hist[]; peers: Peer[]; risk: { period_end: string; risk_score: number; reasons: string[] }[]
}

export default function LoanDetail() {
  const { loanId } = useParams()
  const { data, error, loading } = useApi<Detail>(`/api/loans/${loanId}`)
  if (error) return <div className="err">{error}</div>
  if (loading || !data) return <div className="loading">Loading…</div>
  const l = data.loan
  const chart = data.history.map((h) => ({
    p: h.period_end.slice(0, 7),
    mark: h.mark == null ? null : +(h.mark * 100).toFixed(2),
    rate: h.rate == null ? null : +(h.rate * 100).toFixed(2),
    spread: h.spread == null ? null : +(h.spread * 100).toFixed(2),
    pik: h.pik_rate == null ? null : +(h.pik_rate * 100).toFixed(2),
  }))
  const latestPeers = data.peers.filter((p) => p.period_end === data.peers[0]?.period_end)
  const last = data.history[data.history.length - 1]
  const first = data.history[0]
  const rk = data.risk[data.risk.length - 1]
  const summary = [
    `${titleCase(l.bdc_name)} holds a ${l.instrument_type.replace(/_/g, ' ')} to ${l.issuer_name || 'this borrower'}${last?.cost ? ` with $${(last.cost / 1e6).toFixed(1)}mm at cost` : ''}.`,
    last?.mark != null ? `It is currently marked at ${num(last.mark, 3)}${first?.mark != null && data.history.length > 1 ? ` (${first.mark > last.mark + 0.005 ? 'down' : first.mark < last.mark - 0.005 ? 'up' : 'unchanged'} from ${num(first.mark, 3)} when first seen in ${first.period_end.slice(0, 7)})` : ''}.` : 'No mark is available (cost was not reported).',
    last?.nonaccrual_flag ? 'The lender has it on non-accrual: interest is no longer being counted as income.' : last?.pik_flag ? 'It is paying interest in kind rather than cash.' : '',
    rk ? `Risk score ${rk.risk_score}: ${rk.risk_score >= 100 ? 'already impaired' : rk.risk_score >= 40 ? 'high chance of going bad within a year' : rk.risk_score >= 20 ? 'elevated' : 'low'}${rk.reasons.length ? ` (${rk.reasons.join('; ')})` : ' (no warning signs)'}.` : '',
    latestPeers.length > 1 ? `${latestPeers.length} BDCs hold this borrower; marks range from ${num(Math.min(...latestPeers.map((p) => p.mark ?? 9)), 3)} to ${num(Math.max(...latestPeers.map((p) => p.mark ?? 0)), 3)}.` : '',
  ].filter(Boolean).join(' ')
  return (
    <div>
      <PageHeader eyebrow="Loan" title={l.issuer_name || l.identifier} ticker={l.ticker} lede={summary} />
      <div className="sub">
        Held by <Link to={`/bdcs/${l.cik}`}>{titleCase(l.bdc_name)}{l.ticker ? ` (${l.ticker})` : ''}</Link> · {l.instrument_type.replace(/_/g, ' ')} · loan {l.loan_id}
        {' · '}<Link to={`/borrowers/${encodeURIComponent(l.borrower_key)}`}>every lender to this borrower</Link>
      </div>
      <div className="stats">
        <Stat k="Observed" v={`${l.n_periods} quarters`} d={`${l.first_period} → ${l.last_period}${l.exited ? ` · exited (${l.exit_type})` : ''}`} />
        <Stat k="Last mark" v={num(l.last_mark, 3)} d={`min ${num(l.min_mark, 3)}`} cls={l.last_mark != null && l.last_mark < 0.95 ? 'neg' : ''} />
        <Stat k="Ever non-accrual" v={l.ever_nonaccrual ? 'yes' : 'no'} cls={l.ever_nonaccrual ? 'neg' : ''} />
        {data.risk.length > 0 && (
          <Stat k="Risk score (latest)" v={data.risk[data.risk.length - 1].risk_score} d={data.risk[data.risk.length - 1].reasons.join('; ') || 'no warning signals'} cls={data.risk[data.risk.length - 1].risk_score >= 40 ? 'neg' : ''} />
        )}
      </div>
      <Section title="Across quarters">
      <div className="row">
        <div className="panel">
          <h2 title={G.mark}>Mark and pricing over time (mark on the left axis, rates on the right)</h2>
          <div className="chart">
            <ResponsiveContainer>
              <LineChart data={chart} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="p" />
                <YAxis yAxisId="l" domain={['auto', 'auto']} unit="%" /><YAxis yAxisId="r" orientation="right" unit="%" domain={[0, 'auto']} />
                <Tooltip /><Legend />
                <Line isAnimationActive={false} yAxisId="l" type="monotone" dataKey="mark" name="FV / cost" stroke="#1b7f3b" />
                <Line isAnimationActive={false} yAxisId="r" type="monotone" dataKey="rate" name="rate" stroke="#2563eb" dot={false} />
                <Line isAnimationActive={false} yAxisId="r" type="monotone" dataKey="spread" name="spread" stroke="#9ca3af" dot={false} />
                <Line isAnimationActive={false} yAxisId="r" type="monotone" dataKey="pik" name="PIK" stroke="#c62828" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        {latestPeers.length > 0 && (
          <div className="panel">
            <h2>Every BDC holding this borrower ({latestPeers[0].period_end})</h2>
            <div className="tablewrap"><table className="grid">
              <thead><tr><th className="l">BDC</th><th className="l">Type</th><th>FV $mm</th><th>Cost $mm</th><th>Mark</th><th>vs peers</th><th>NA</th></tr></thead>
              <tbody>
                {latestPeers.map((p, i) => (
                  <tr key={i}><td className="l"><Link to={`/bdcs/${p.cik}`}>{p.ticker ?? titleCase(p.name)}</Link></td><td className="l">{p.instrument_type}</td>
                    <td>{mm(p.fv)}</td><td>{mm(p.cost)}</td><td>{num(p.mark, 3)}</td>
                    <td className={cls(p.mark_vs_peers, true)}>{signed(p.mark_vs_peers == null ? null : p.mark_vs_peers * 100, 1, '%')}</td>
                    <td>{p.nonaccrual ? <span className="tag flag">NA</span> : ''}</td></tr>
                ))}
              </tbody>
            </table></div>
          </div>
        )}
      </div>
      </Section>
      <Section title="Quarterly history">
      <Explain kind="quiet"><p><b>How to read this.</b> One row per quarter the loan appeared in the schedule, newest first, with the exact identifier text the BDC filed and any footnote. "Match" is how the row was linked to the previous quarter: exact text, normalised text, borrower plus instrument, or fuzzy name.</p></Explain>
      <div className="panel">
        <div className="tablewrap">
          <table className="grid">
            <thead><tr><th className="l">Period</th><th>FV $mm</th><th>Cost $mm</th><th>Principal</th><th>Mark</th><th>Δ</th><th>Rate</th><th>Spread</th><th>Floor</th><th>PIK</th><th className="l">Maturity</th><th className="l">Flags</th><th className="l">Match</th><th className="l">Identifier / footnotes</th></tr></thead>
            <tbody>
              {[...data.history].reverse().map((h) => (
                <tr key={h.period_end}>
                  <td className="l">{h.period_end}</td><td>{mm(h.fair_value)}</td><td>{mm(h.cost)}</td><td>{mm(h.principal)}</td>
                  <td className={h.mark != null && h.mark < 0.95 ? 'neg' : ''}>{num(h.mark, 3)}</td>
                  <td className={cls(h.mark_chg)}>{signed(h.mark_chg, 3)}</td>
                  <td>{pct(h.rate, 2)}</td><td>{pct(h.spread, 2)}</td><td>{pct(h.floor_rate, 2)}</td><td>{pct(h.pik_rate, 2)}</td>
                  <td className="l">{h.maturity ?? ''}</td>
                  <td className="l">{h.nonaccrual_flag && <span className="tag flag">non-accrual</span>}{h.pik_flag && <span className="tag info">PIK</span>}{h.spread_up && <span className="tag info">spread↑</span>}{h.maturity_extended && <span className="tag info">extended</span>}</td>
                  <td className="l muted">{h.match_method}</td>
                  <td className="l wrap small muted">{h.identifier}{h.footnote_text ? ` — ${h.footnote_text}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      </Section>
    </div>
  )
}
