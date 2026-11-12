// Trade history of the pool wallet, for the dashboard's "Trading activity" section and the Telegram /today
// report. Anyone can call it; it only reads public chain data.
//
// A trade is a buy leg (a tx the wallet sent that moved USDT out) followed by a sell leg (a tx the wallet
// sent that brought USDT back). Profit = USDT back - USDT out. Direct USDT transfers (withdrawals) and
// transfers the wallet did not send (deposits) are ignored.
//
// Where the data comes from: the wallet's USDT transfers are read from a Celo node (chain.ts) and stored in the
// database (pool_tx, see 013_pool_tx.sql). Each call first reads the few blocks made since the last call, then
// answers from the database. A schedule calls this every 2 minutes ({cron: true}) so the history stays current.
// If the node cannot be reached, the stored history is served, flagged `stale` with its age. An empty history
// is only ever reported when the chain was read and really has no trades: a failure is an error, never
// "no trades" (on 9 Nov a fallback explorer's "chain not supported" was shown as an empty season).
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'
import { makeRpc, syncChain, type Known, type Row, type State, type Store } from './chain.ts'

const RPC_URL = 'https://forno.celo.org' // the public Celo node the keeper and alerts already use
const STALE_MS = 10 * 60_000 // older than this, the answer says how old it is
const RETRY_GAP_MS = 20_000 // at most one read of the chain per 20 seconds, however often this is called
const BUDGET_MS = 6_000 // time a normal call may spend catching up
const CRON_BUDGET_MS = 40_000 // time a scheduled call may spend (first load, daily re-read)
const MAX_DAYS = 90 // enough for the dashboard's weekly view

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
}
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } })

// deno-lint-ignore no-explicit-any
function makeStore(db: any): Store {
  // deno-lint-ignore no-explicit-any
  const must = (r: { data: any; error: any }, what: string) => {
    if (r.error) throw new Error(`database (${what}): ${r.error.message}`)
    return r.data
  }
  return {
    async load(wallet): Promise<State | null> {
      const d = must(await db.from('pool_sync').select('from_block, block, since_ms, synced_at, audited_at').eq('wallet', wallet).maybeSingle(), 'load')
      return d ? { from_block: Number(d.from_block), block: Number(d.block), since_ms: Number(d.since_ms), synced_at: d.synced_at, audited_at: d.audited_at } : null
    },
    async init(wallet, fromBlock, sinceMs) {
      must(await db.from('pool_sync').upsert({ wallet, from_block: fromBlock, block: fromBlock - 1, block_time: null, since_ms: sinceMs, synced_at: null, audited_at: null, last_error: null }), 'init')
    },
    async known(wallet, lo, hi): Promise<Known> {
      const d = must(await db.from('pool_tx').select('hash, sent, to_addr').eq('wallet', wallet).gte('block', lo).lte('block', hi).limit(1000), 'known')
      // deno-lint-ignore no-explicit-any
      return new Map((d ?? []).map((r: any) => [r.hash, { sent: r.sent === true, to: String(r.to_addr ?? '') }]))
    },
    async apply(wallet, lo, hi, hiTimeMs, rows: Row[], replace) {
      must(await db.rpc('pool_apply', { p_wallet: wallet, p_lo: lo, p_hi: hi, p_hi_time: new Date(hiTimeMs).toISOString(), p_rows: rows, p_replace: replace }), 'apply')
    },
    async reached(wallet, atIso, audited) {
      must(await db.from('pool_sync').update({ synced_at: atIso, last_error: null, ...(audited ? { audited_at: atIso } : {}) }).eq('wallet', wallet), 'reached')
    },
  }
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })

  // Public: members now sign in with a wallet, and this only reads public chain data.
  const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const { data: s, error: sErr } = await db.from('settings').select('bot_wallet, started_at, season_started_at').eq('id', 1).single()
  if (sErr) return reply(200, { error: `Could not read trade history: ${sErr.message}` })
  const wallet = String(s?.bot_wallet ?? '').toLowerCase()
  if (!/^0x[0-9a-f]{40}$/.test(wallet)) return reply(200, { error: 'No pool wallet set' })

  const now = Date.now()
  // the current season only: a new season starts the trade history afresh
  const start = s?.season_started_at ? Date.parse(s.season_started_at) : s?.started_at ? Date.parse(`${s.started_at}T00:00:00Z`) : now - 7 * 86_400_000
  const since = Math.max(start, now - MAX_DAYS * 86_400_000)
  const cron = req.method === 'POST' ? await req.json().then((b) => b?.cron === true).catch(() => false) : false

  // 1. Catch up with the chain (best effort; whatever happens, step 2 answers from what is stored).
  let syncError: string | null = null
  try {
    const { data: st, error } = await db.from('pool_sync').select('tried_at').eq('wallet', wallet).maybeSingle()
    if (error) throw new Error(`database (state): ${error.message}`)
    const triedRecently = st?.tried_at != null && now - Date.parse(st.tried_at) < RETRY_GAP_MS
    if (!triedRecently) {
      if (st) await db.from('pool_sync').update({ tried_at: new Date(now).toISOString() }).eq('wallet', wallet)
      await syncChain(makeRpc(RPC_URL), makeStore(db), wallet, since, { budgetMs: cron ? CRON_BUDGET_MS : BUDGET_MS, audit: cron })
      await db.from('pool_sync').update({ tried_at: new Date().toISOString(), last_error: null }).eq('wallet', wallet)
    }
  } catch (e) {
    syncError = String((e as Error).message ?? e).slice(0, 200)
    await db.from('pool_sync').update({ tried_at: new Date().toISOString(), last_error: syncError }).eq('wallet', wallet)
  }

  // 2. Answer from the database.
  const { data: st, error: stErr } = await db.from('pool_sync').select('block_time, synced_at').eq('wallet', wallet).maybeSingle()
  if (stErr) return reply(200, { error: `Could not read trade history: ${stErr.message}` })
  if (!st?.synced_at || !st?.block_time) {
    // the first load has not finished: say so, never "no trades"
    return reply(200, { error: syncError ? `Could not read trade history: ${syncError}` : 'Trade history is being loaded from the chain. Try again in a minute.' })
  }
  const { data: trades, error: tErr } = await db.rpc('pool_trades', { p_wallet: wallet, p_since: since })
  if (tErr || !Array.isArray(trades)) return reply(200, { error: `Could not read trade history: ${tErr?.message ?? 'unexpected answer from the database'}` })

  const done = Date.now()
  const asOf = Date.parse(st.block_time)
  const age = done - asOf
  return reply(200, { since, now: done, asOf, trades, ...(age > STALE_MS ? { stale: true, staleAgeMs: age } : {}) })
})
