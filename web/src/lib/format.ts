export const pct = (v: number | null | undefined, d = 1) =>
  v == null ? '' : `${(v * 100).toFixed(d)}%`
export const num = (v: number | null | undefined, d = 2) => (v == null ? '' : v.toFixed(d))
export const mm = (v: number | null | undefined) =>
  v == null ? '' : `${(v / 1e6).toLocaleString(undefined, { maximumFractionDigits: 1 })}`
export const bn = (v: number | null | undefined) =>
  v == null ? '' : `$${(v / 1e9).toFixed(2)}bn`
export const signed = (v: number | null | undefined, d = 2, suffix = '') =>
  v == null ? '' : `${v > 0 ? '+' : ''}${v.toFixed(d)}${suffix}`
export const signedPct = (v: number | null | undefined, d = 1) =>
  v == null ? '' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(d)}%`
export const cls = (v: number | null | undefined, invert = false) => {
  if (v == null || v === 0) return ''
  const good = invert ? v < 0 : v > 0
  return good ? 'pos' : 'neg'
}
export const bucketLabel: Record<string, string> = {
  '1_ge98': '≥98', '2_95_98': '95–98', '3_90_95': '90–95', '4_80_90': '80–90', '5_lt80': '<80',
}
