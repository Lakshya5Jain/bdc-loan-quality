export default function Stat({ k, v, d, cls }: { k: string; v: string | number; d?: string; cls?: string }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className={`v ${cls ?? ''}`}>{v}</div>
      {d && <div className="d">{d}</div>}
    </div>
  )
}
