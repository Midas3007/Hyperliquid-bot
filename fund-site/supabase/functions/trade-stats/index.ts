// Trade history of the pool wallet, rebuilt from the chain (Etherscan-style explorer API), for the dashboard's
// "Trading activity" section and the Telegram /today report. Anyone can call it; it only reads public chain data.
//
// A trade is a buy leg (a tx the wallet sent that moved USDT out) followed by a sell leg (a tx the wallet
// sent that brought USDT back). Profit = USDT back - USDT out. Direct USDT transfers (withdrawals) and
// transfers the wallet did not send (deposits) are ignored.
//
// The explorers rate-limit (HTTP 429). To stay reliable we (1) retry a 429/5xx after a growing wait, honouring
// any Retry-After header; (2) fetch the two queries one after another, not in parallel, to halve the request
// rate; (3) fall back to a second explorer if the first keeps failing; (4) cache the last good result and, if a
// refresh fails, serve that (flagged `stale`) instead of an error, so the report always shows something.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'

// Etherscan-compatible explorers for Celo, tried in order (both keyless: Blockscout, then Routescan).
const EXPLORERS = [
  'https://celo.blockscout.com/api',
  'https://api.routescan.io/v2/network/mainnet/evm/42220/etherscan/api',
]
const USDT = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
const FRESH_MS = 10 * 60_000 // serve the cache without refetching within this window
const MAX_DAYS = 90 // enough for the dashboard's weekly view
let cache: { key: string; at: number; body: any } | null = null

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
}
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } })
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// One explorer page, with backoff on 429 / 5xx (1s, 2s, 4s, 8s; or Retry-After, capped).
async function fetchPage(base: string, q: URLSearchParams) {
  let wait = 1000
  for (let attempt = 0; ; attempt++) {
    let r: Response
    try {
      r = await fetch(`${base}?${q}`)
    } catch (e) {
      if (attempt >= 4) throw e
      await sleep(wait); wait = Math.min(wait * 2, 8000); continue
    }
    if (r.ok) return await r.json()
    if ((r.status === 429 || r.status >= 500) && attempt < 4) {
      const ra = Number(r.headers.get('retry-after'))
      await sleep(Number.isFinite(ra) && ra > 0 ? Math.min(ra * 1000, 15000) : wait)
      wait = Math.min(wait * 2, 8000)
      continue
    }
    throw new Error(`Explorer returned ${r.status}`)
  }
}

// Newest first, until rows are older than stopBefore (ms).
async function explorer(base: string, params: Record<string, string>, stopBefore: number) {
  const out: any[] = []
  for (let page = 1; page <= 20; page++) {
    const q = new URLSearchParams({ module: 'account', ...params, page: String(page), offset: '1000', sort: 'desc' })
    const j = await fetchPage(base, q)
    const rows = Array.isArray(j.result) ? j.result : []
    out.push(...rows)
    if (rows.length < 1000 || Number(rows[rows.length - 1].timeStamp) * 1000 < stopBefore) break
  }
  return out
}

// Try each explorer in turn; fetch the two queries sequentially to be gentle on the rate limit.
async function loadAll(wallet: string, since: number) {
  let lastErr: unknown
  for (const base of EXPLORERS) {
    try {
      const txs = await explorer(base, { action: 'txlist', address: wallet }, since)
      const transfers = await explorer(base, { action: 'tokentx', address: wallet, contractaddress: USDT }, since)
      return { transfers, txs }
    } catch (e) {
      lastErr = e // this explorer failed after its retries; try the next one
    }
  }
  throw lastErr ?? new Error('all explorers failed')
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })

  // Public: members now sign in with a wallet, and this only reads public chain data (cached).
  const url = Deno.env.get('SUPABASE_URL')!
  const db = createClient(url, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const { data: s } = await db.from('settings').select('bot_wallet, started_at, season_started_at').eq('id', 1).single()
  const wallet = String(s?.bot_wallet ?? '').toLowerCase()
  if (!/^0x[0-9a-f]{40}$/.test(wallet)) return reply(200, { error: 'No pool wallet set' })

  const now = Date.now()
  // the current season only: a new season starts the trade history afresh
  const start = s?.season_started_at ? Date.parse(s.season_started_at) : s?.started_at ? Date.parse(`${s.started_at}T00:00:00Z`) : now - 7 * 86_400_000
  const since = Math.max(start, now - MAX_DAYS * 86_400_000)
  const key = `${wallet}:${since}`
  if (cache && cache.key === key && now - cache.at < FRESH_MS) return reply(200, cache.body)

  try {
    const { transfers, txs } = await loadAll(wallet, since)

    // Transactions the wallet itself sent, oldest first.
    const sent = txs
      .filter((t) => String(t.from).toLowerCase() === wallet && t.isError !== '1' && Number(t.timeStamp) * 1000 >= since)
      .map((t) => ({ hash: String(t.hash).toLowerCase(), to: String(t.to ?? '').toLowerCase(), t: Number(t.timeStamp) * 1000 }))
      .sort((a, b) => a.t - b.t)

    // Net USDT change per transaction.
    const net = new Map<string, number>()
    for (const x of transfers) {
      const hash = String(x.hash).toLowerCase()
      const v = Number(x.value) / 1e6
      const sign = String(x.to).toLowerCase() === wallet ? 1 : String(x.from).toLowerCase() === wallet ? -1 : 0
      net.set(hash, (net.get(hash) ?? 0) + sign * v)
    }

    const trades: { t: number; size: number; profit: number }[] = []
    let open = 0
    for (const tx of sent) {
      const d = net.get(tx.hash) ?? 0
      if (Math.abs(d) < 1e-9 || tx.to === USDT) continue // approvals, or a direct USDT transfer (withdrawal)
      if (d < 0) open += -d
      else if (open > 0) {
        trades.push({ t: tx.t, size: Math.round(open * 1e6) / 1e6, profit: Math.round((d - open) * 1e6) / 1e6 })
        open = 0
      }
    }

    const body = { since, now, trades }
    cache = { key, at: now, body }
    return reply(200, body)
  } catch (e) {
    // Explorers are down or still rate-limiting: serve the last good result rather than a hard error.
    if (cache && cache.key === key) return reply(200, { ...cache.body, stale: true, staleAgeMs: now - cache.at })
    return reply(200, { error: `Could not read trade history: ${(e as Error).message}` })
  }
})
