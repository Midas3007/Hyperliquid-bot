// GET /api/market - live Hyperliquid tape for the terminal: the top perps by 24h volume (public data, no keys).
import type { Config } from "@netlify/functions"

type Row = { coin: string; mark: number; chg: number; funding8h: number; oiUsd: number; volUsd: number }

export default async () => {
  try {
    const r = await fetch("https://api.hyperliquid.xyz/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "metaAndAssetCtxs" }),
    })
    if (!r.ok) throw new Error(`hyperliquid ${r.status}`)
    const [meta, ctxs] = (await r.json()) as [any, any[]]
    const rows: Row[] = []
    meta.universe.forEach((u: any, i: number) => {
      const c = ctxs[i]
      if (!c || u.isDelisted) return
      const mark = Number(c.markPx), prev = Number(c.prevDayPx)
      if (!(mark > 0)) return
      rows.push({
        coin: u.name, mark,
        chg: prev > 0 ? (mark / prev - 1) * 100 : 0,
        funding8h: Number(c.funding) * 8 * 100,
        oiUsd: Number(c.openInterest) * mark,
        volUsd: Number(c.dayNtlVlm),
      })
    })
    rows.sort((a, b) => b.volUsd - a.volUsd)
    return Response.json({ at: Date.now(), rows: rows.slice(0, 24) },
                         { headers: { "Cache-Control": "public, max-age=20" } })
  } catch (e) {
    return Response.json({ at: Date.now(), rows: [], error: String(e).slice(0, 120) }, { status: 502 })
  }
}

export const config: Config = { path: "/api/market", method: "GET" }
