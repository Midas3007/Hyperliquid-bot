// Bot settings switched from Telegram (stored in the fund's Supabase, read through the public bot_settings()
// call). Read at most every 2 minutes; if a read fails the last known values stay. Off unless FUND_SUPABASE_URL
// and FUND_SUPABASE_KEY are set, in which case every setting keeps its default.
const URL_ = process.env.FUND_SUPABASE_URL
const KEY = process.env.FUND_SUPABASE_KEY
const CHECK_MS = 2 * 60_000

let last: Record<string, any> = {}
let checkedAt = 0

export async function botSettings(): Promise<Record<string, any>> {
  if (URL_ && KEY && Date.now() - checkedAt >= CHECK_MS) {
    checkedAt = Date.now()
    try {
      const r = await fetch(`${URL_}/rest/v1/rpc/bot_settings`, {
        method: 'POST', headers: { apikey: KEY, Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' }, body: '{}',
        signal: AbortSignal.timeout(10_000),
      })
      if (r.ok) { const j = await r.json(); if (j && typeof j === 'object') last = j }
    } catch { /* keep the last known settings */ }
  }
  return last
}
