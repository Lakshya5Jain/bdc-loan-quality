/** "-0.0" reads as a bug; a value that rounds to zero is shown as zero. */
const z = (s: string) => s.replace(/^-(?=0(?:[.,]0+)?%?$)/, '')
export const pct = (v: number | null | undefined, d = 1) =>
  v == null ? '' : z(`${(v * 100).toFixed(d)}%`)
export const num = (v: number | null | undefined, d = 2) => (v == null ? '' : z(v.toFixed(d)))
export const mm = (v: number | null | undefined) =>
  v == null ? '' : z((v / 1e6).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }))
export const bn = (v: number | null | undefined) =>
  v == null ? '' : `$${z((v / 1e9).toFixed(2))}bn`
export const signed = (v: number | null | undefined, d = 2, suffix = '') => {
  if (v == null) return ''
  const s = z(v.toFixed(d))
  return `${v > 0 && s !== '0' && !/^0\.0+$/.test(s) ? '+' : ''}${s}${suffix}`
}
export const signedPct = (v: number | null | undefined, d = 1) => {
  if (v == null) return ''
  const s = z((v * 100).toFixed(d))
  return `${v > 0 && !/^0(\.0+)?$/.test(s) ? '+' : ''}${s}%`
}

/** SEC filer names arrive in capitals ("GOLDMAN SACHS BDC, INC."); keep only real abbreviations upper-case. */
const KEEP_UPPER = new Set(['BDC', 'LLC', 'LP', 'FS', 'KKR', 'SLR', 'PGIM', 'BCP', 'MSC', 'OFS', 'CION', 'TCP', 'BSP', 'HPS', 'TCW', 'TPG', 'AGL', 'CCS', 'MSD', 'NC', 'JV', 'SCP', 'BNY', 'NMF', 'BDCA', 'SPV', 'AB', 'HMS', 'ABS', 'US', 'USA', 'CCAP', 'TSLX', 'TCPC', 'SBA', 'NAV', 'SVB', 'BC', 'AI', 'GP'])
const ROMAN = /^(?:i{1,3}|iv|v|vi{1,3}|ix|x{1,2}|xi{1,3})$/i
export const titleCase = (n: string | null | undefined) =>
  (n ?? '').split(' ').map((w) => {
    const core = w.replace(/[.,()&]/g, '')
    if (!core || /\d/.test(core)) return w
    if (KEEP_UPPER.has(core.toUpperCase()) || ROMAN.test(core)) return w.toUpperCase()
    return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()
  }).join(' ')
export const cls = (v: number | null | undefined, invert = false) => {
  if (v == null || v === 0) return ''
  const good = invert ? v < 0 : v > 0
  return good ? 'pos' : 'neg'
}
export const bucketLabel: Record<string, string> = {
  '1_ge98': '≥98', '2_95_98': '95–98', '3_90_95': '90–95', '4_80_90': '80–90', '5_lt80': '<80',
}

/** "//fasb.org/us-gaap/2026#HealthcareSectorMember" -> "Healthcare"; free text passes through title-cased. */
export const industryLabel = (v: string | null | undefined): string => {
  if (!v) return ''
  let t = v.includes('#') ? v.slice(v.lastIndexOf('#') + 1) : v
  t = t.replace(/(Sector|Industry)?Member$/, '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
  return titleCase(t.trim())
}
