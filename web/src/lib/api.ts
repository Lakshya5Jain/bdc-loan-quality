import { useEffect, useState } from 'react'

/**
 * Data access for the pages.
 *
 * Default mode talks to the FastAPI server (`/api/...`, proxied by Vite in dev).
 * When built with `VITE_DATA_MODE=static` (see `npm run build:static`, used on Vercel) the
 * same `/api/...` URLs are answered from the JSON tree written by `uv run soi export-static`
 * under `/data/`, decoded and filtered here so the pages need no changes.
 */
const STATIC = import.meta.env.VITE_DATA_MODE === 'static'
const DATA_BASE = '/data'

export async function getJson<T>(url: string): Promise<T> {
  if (STATIC) return staticGet<T>(url)
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url}`)
  return (await r.json()) as T
}

export function useApi<T>(url: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!url) return
    let alive = true
    setLoading(true)
    setError(null)
    getJson<T>(url)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [url])
  return { data, error, loading }
}

// ---------------------------------------------------------------------------------------------
// Static mode
// ---------------------------------------------------------------------------------------------

type Row = Record<string, unknown>
type Table = { cols: string[]; rows: unknown[][] }
type LoanShard = {
  footnotes: string[]
  history_cols: string[]
  loans: Record<string, { loan: Row; history: unknown[][] }>
}
type BorrowerShard = {
  loan_cols: string[]
  mark_cols: string[]
  borrowers: Record<string, { loans: unknown[][]; marks: unknown[][] }>
}

/** 32-bit FNV-1a over UTF-8 bytes; must match `fnv1a` in src/soi/export_static.py. */
export function fnv1a(s: string): number {
  let h = 0x811c9dc5
  for (const b of new TextEncoder().encode(s)) {
    h ^= b
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h >>> 0
}
export const loanShard = (id: string) => (fnv1a(id) % 4096).toString(16).padStart(3, '0')
export const borrowerShard = (key: string) => (fnv1a(key) % 256).toString(16).padStart(2, '0')

const cache = new Map<string, Promise<unknown>>()
function load<T>(path: string): Promise<T> {
  let p = cache.get(path)
  if (!p) {
    p = fetch(DATA_BASE + path).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${path}`)
      return r.json()
    })
    p.catch(() => cache.delete(path))
    cache.set(path, p)
  }
  return p as Promise<T>
}

/** Expand columnar rows to objects; `fn` becomes `footnote_text` via the footnote list. */
function expand<T = Row>(cols: string[], rows: unknown[][], footnotes: string[] = []): T[] {
  return rows.map((r) => {
    const o: Row = {}
    cols.forEach((c, i) => {
      const v = r[i]
      if (c === 'fn') o.footnote_text = v == null ? null : footnotes[v as number]
      else o[c] = v
    })
    return o as T
  })
}

/** Same predicates as the `flag` query parameter in api/main.py. */
const FLAG_FILTERS: Record<string, (l: Row) => boolean> = {
  stressed: (l) => !!l.is_stressed,
  nonaccrual: (l) => !!l.nonaccrual_flag,
  new_nonaccrual: (l) => !!l.new_nonaccrual,
  markdown: (l) => typeof l.mark_chg === 'number' && l.mark_chg < -0.02,
  new: (l) => !!l.is_new,
  pik: (l) => !!l.pik_flag,
  debt: (l) => !!l.is_debt,
}

const cmpNullLast = (a: unknown, b: unknown) => {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1
  return (a as number) - (b as number)
}

async function staticGet<T>(url: string): Promise<T> {
  const u = new URL(url, 'http://static.local')
  const [root, a, b, c] = u.pathname.split('/').filter(Boolean)
  if (root !== 'api') throw new Error(`no static route for ${url}`)

  if (a === 'status') return load<T>('/status.json')
  if (a === 'screen') return load<T>('/screen.json')
  if (a === 'strategy') return load<T>('/strategy.json')
  if (a === 'stale-marks') return load<T>('/stale-marks.json')
  if (a === 'lab') return load<T>('/lab.json')
  if (a === 'health') return { ok: true } as T
  if (a === 'insights' && b) return load<T>(`/insights/${b}.json`)

  if (a === 'bdcs' && !b) {
    const rows = await load<Row[]>('/bdcs.json')
    const publicOnly = u.searchParams.get('public_only') === 'true'
    return (publicOnly ? rows.filter((r) => r.is_public) : rows) as T
  }
  if (a === 'bdcs' && b && !c) return load<T>(`/bdcs/${b}.json`)
  if (a === 'bdcs' && b && c === 'loans') {
    let period = u.searchParams.get('period')
    if (!period) {
      const d = await load<{ quarters: { period_end: string }[] }>(`/bdcs/${b}.json`)
      period = d.quarters[d.quarters.length - 1]?.period_end ?? null
      if (!period) throw new Error(`404 no holdings for BDC ${b}`)
    }
    const f = await load<Table & { footnotes: string[] }>(`/bdcs/${b}/loans/${period}.json`)
    const rows = expand(f.cols, f.rows, f.footnotes)
    const pred = FLAG_FILTERS[u.searchParams.get('flag') ?? '']
    const limit = Number(u.searchParams.get('limit') ?? 5000)
    return (pred ? rows.filter(pred) : rows).slice(0, limit) as T
  }

  if (a === 'loans' && b) {
    const id = decodeURIComponent(b)
    const shard = await load<LoanShard>(`/loans/${loanShard(id)}.json`)
    const e = shard.loans[id]
    if (!e) throw new Error(`404 unknown loan ${id}`)
    const history = expand(shard.history_cols, e.history, shard.footnotes)
    const key = String(e.loan.borrower_key)
    const bs = await load<BorrowerShard>(`/borrowers/${borrowerShard(key)}.json`)
    const marks = bs.borrowers[key] ? expand(bs.mark_cols, bs.borrowers[key].marks) : []
    const peers = marks
      .map((m) => ({
        cik: m.cik, ticker: m.ticker, name: m.bdc_name, period_end: m.period_end,
        instrument_type: m.instrument_type, fv: m.fv, cost: m.cost, mark: m.mark,
        nonaccrual: m.nonaccrual, mark_vs_peers: m.mark_vs_peers,
      }))
      .sort((x, y) =>
        x.period_end === y.period_end
          ? cmpNullLast(x.mark, y.mark)
          : (x.period_end as string) < (y.period_end as string) ? 1 : -1,
      )
    const risk = (e as { risk?: Row[] }).risk ?? []
    return { loan: e.loan, history, peers, risk } as T
  }

  if (a === 'borrowers' && !b) {
    const q = (u.searchParams.get('q') ?? '').toLowerCase()
    const limit = Number(u.searchParams.get('limit') ?? 50)
    const idx = await load<Table>('/borrowers/index.json')
    const hit = (r: Row) =>
      String(r.borrower_key ?? '') !== '' &&
      (String(r.borrower_key ?? '').toLowerCase().includes(q) ||
        String(r.issuer_name ?? '').toLowerCase().includes(q))
    // index.json is already ordered by n_bdcs desc, n_loans desc
    return expand(idx.cols, idx.rows).filter(hit).slice(0, limit) as T
  }
  if (a === 'borrowers' && b) {
    const key = decodeURIComponent(b)
    const bs = await load<BorrowerShard>(`/borrowers/${borrowerShard(key)}.json`)
    const e = bs.borrowers[key]
    if (!e || e.loans.length === 0) throw new Error(`404 unknown borrower ${key}`)
    return {
      borrower_key: key,
      loans: expand(bs.loan_cols, e.loans),
      marks: expand(bs.mark_cols, e.marks),
    } as T
  }

  throw new Error(`no static route for ${url}`)
}
