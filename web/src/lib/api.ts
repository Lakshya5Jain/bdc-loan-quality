import { useEffect, useState } from 'react'

export async function getJson<T>(url: string): Promise<T> {
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
