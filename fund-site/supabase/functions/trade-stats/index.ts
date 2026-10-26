// Trade history of the pool wallet, rebuilt from the chain (Celo Blockscout API), for the dashboard's
// "Trading activity" section. Any signed-in member can call it; it only reads public chain data.
//
// A trade is a buy leg (a tx the wallet sent that moved USDT out) followed by a sell leg (a tx the wallet
// sent that brought USDT back). Profit = USDT back - USDT out. Direct USDT transfers (withdrawals) and
// transfers the wallet did not send (deposits) are ignored.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'

const EXPLORER = 'https://celo.blockscout.com/api'
const USDT = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
const CACHE_MS = 60_000
const MAX_DAYS = 30
let cache: { key: string; at: number; body: unknown } | null = null

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
}
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } })

// Newest first, until rows are older than stopBefore (ms).
async function explorer(params: Record<string, string>, stopBefore: number) {
  const out: any[] = []
  for (let page = 1; page <= 20; page++) {
    const q = new URLSearchParams({ module: 'account', ...params, page: String(page), offset: '1000', sort: 'desc' })
    const r = await fetch(`${EXPLORER}?${q}`)
    if (!r.ok) throw new Error(`Explorer returned ${r.status}`)
    const j = await r.json()
    const rows = Array.isArray(j.result) ? j.result : []
    out.push(...rows)
    if (rows.length < 1000 || Number(rows[rows.length - 1].timeStamp) * 1000 < stopBefore) break
  }
  return out
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })

  const url = Deno.env.get('SUPABASE_URL')!
  const caller = createClient(url, Deno.env.get('SUPABASE_ANON_KEY')!, {
    global: { headers: { Authorization: req.headers.get('Authorization') ?? '' } },
  })
  const { data: memberId } = await caller.rpc('my_member_id')
  if (!memberId) return reply(403, { error: 'Members only' })

  const db = createClient(url, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const { data: s } = await db.from('settings').select('bot_wallet, started_at').eq('id', 1).single()
  const wallet = String(s?.bot_wallet ?? '').toLowerCase()
  if (!/^0x[0-9a-f]{40}$/.test(wallet)) return reply(200, { error: 'No pool wallet set' })

  const now = Date.now()
  const start = s?.started_at ? Date.parse(`${s.started_at}T00:00:00Z`) : now - 7 * 86_400_000
  const since = Math.max(start, now - MAX_DAYS * 86_400_000)
  const key = `${wallet}:${since}`
  if (cache && cache.key === key && now - cache.at < CACHE_MS) return reply(200, cache.body)

  try {
    const [transfers, txs] = await Promise.all([
      explorer({ action: 'tokentx', address: wallet, contractaddress: USDT }, since),
      explorer({ action: 'txlist', address: wallet }, since),
    ])

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
    return reply(200, { error: `Could not read trade history: ${(e as Error).message}` })
  }
})
