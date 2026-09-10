import type { ReactNode } from 'react'

/** Page header: eyebrow, title, and a plain-English paragraph on what the page shows. */
export function PageHeader({ eyebrow, title, ticker, lede }: { eyebrow: string; title: ReactNode; ticker?: string | null; lede?: ReactNode }) {
  return (
    <header>
      <div className="eyebrow">{eyebrow}</div>
      <h1>{title}{ticker && <span className="ticker">{ticker}</span>}</h1>
      {lede && <p className="lede">{lede}</p>}
    </header>
  )
}

/** A short "how to read this" or caution note. */
export function Explain({ kind = 'info', children }: { kind?: 'info' | 'warn' | 'quiet'; children: ReactNode }) {
  return <div className={`explain ${kind === 'info' ? '' : kind}`}>{children}</div>
}

/** A titled section with an optional right-hand meta line (dates, counts). */
export function Section({ title, meta, children }: { title: ReactNode; meta?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head"><h2>{title}</h2>{meta && <div className="meta">{meta}</div>}</div>
      {children}
    </section>
  )
}
