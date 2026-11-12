// Trade history of the pool wallet, rebuilt from the chain (Celo Blockscout API), for the dashboard's
// "Trading activity" section and the Telegram /today report. Anyone can call it; it only reads public chain data.
//
// A trade is a buy leg (a tx the wallet sent that moved USDT out) followed by a sell leg (a tx the wallet
// sent that brought USDT back). Profit = USDT back - USDT out. Direct USDT transfers (withdrawals) and
// transfers the wallet did not send (deposits) are ignored.
//
// The explorer rate-limits (HTTP 429). To stay reliable we (1) retry after a growing wait, honouring any
// Retry-After header; (2) fetch the two queries one after another, not in parallel; (3) cache the last good
// result and, if a refresh fails, serve that (flagged `stale`) instead of an error.
//
// An answer only counts as data when `result` is a list. Anything else (an error message, null, a rate-limit
// note sent with HTTP 200) is a failure, never "no trades": on 9 Nov a second explorer answered
// {"result":null,"message":"chain not supported"} and that was shown as an empty history.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'

const EXPLORER = 'https://celo.blockscout.com/api'
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

// One explorer page: the list of rows, or an error. Retries a 429 / 5xx / network error / malformed answer
// after 1s, 2s, 4s, 8s (or Retry-After, capped), then gives up.
async function fetchPage(q: URLSearchParams): Promise<any[]> {
  let wait = 1000
  let last = 'no answer'
  for (let attempt = 0; attempt < 5; attempt++) {
    if (attempt > 0) { await sleep(wait); wait = Math.min(wait * 2, 8000) }
    let r: Response
    try {
      r = await fetch(`${EXPLORER}?${q}`)
    } catch (e) {
      last = `network: ${(e as Error).message}`
      continue
    }
    if (r.status === 429 || r.status >= 500) {
      last = `Explorer returned ${r.status}`
      const ra = Number(r.headers.get('retry-after'))
      if (Number.isFinite(ra) && ra > 0) wait = Math.min(ra * 1000, 15000)
      continue
    }
    if (!r.ok) throw new Error(`Explorer returned ${r.status}`)
    const j = await r.json().catch(() => null)
    if (j && Array.isArray(j.result)) return j.result // "No transactions found" is a list too: []
    last = `Explorer said: ${String(j?.message ?? j?.result ?? 'unreadable answer').slice(0, 80)}`
  }
  throw new Error(last)
}

// Newest first, until rows are older than stopBefore (ms).
async function explorer(params: Record<string, string>, stopBefore: number) {
  const out: any[] = []
  for (let page = 1; page <= 20; page++) {
    const q = new URLSearchParams({ module: 'account', ...params, page: String(page), offset: '1000', sort: 'desc' })
    const rows = await fetchPage(q)
    out.push(...rows)
    if (rows.length < 1000 || Number(rows[rows.length - 1].timeStamp) * 1000 < stopBefore) break
  }
  return out
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
    // one after the other, to be gentle on the explorer's rate limit
    const txs = await explorer({ action: 'txlist', address: wallet }, since)
    const transfers = await explorer({ action: 'tokentx', address: wallet, contractaddress: USDT }, since)

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
    // The explorer is down or still rate-limiting: serve the last good result rather than a hard error.
    if (cache && cache.key === key) return reply(200, { ...cache.body, stale: true, staleAgeMs: now - cache.at })
    return reply(200, { error: `Could not read trade history: ${(e as Error).message}` })
  }
})
