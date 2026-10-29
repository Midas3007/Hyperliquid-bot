// Midas's addition: pause new trades around the fund's daily settlement, so the bot wallet is flat (all USDT)
// when the FundVault keeper values it at 20:07 UTC. From SETTLE_HOUR_UTC (20) until the keeper reports today's
// settlement done, the desk only sells leftovers (under the usual loss guard) and opens nothing new. If the
// keeper is waiting (for example, not enough USDT for payouts), the pause lasts until it settles, at most until
// midnight UTC. Off unless FUND_SUPABASE_URL and FUND_SUPABASE_KEY (the fund site's public anon key) are set.
const URL_ = process.env.FUND_SUPABASE_URL
const KEY = process.env.FUND_SUPABASE_KEY
const HOUR = Number(process.env.SETTLE_HOUR_UTC ?? '20')
const CHECK_MS = 60_000

export const settlePauseOn = !!(URL_ && KEY)

/** True while new trades should wait: after the settle hour, until the keeper's settle day is today (all UTC). */
export function inSettlePause(now: Date, hour: number, settleDay: string | null) {
  return now.getUTCHours() >= hour && settleDay !== now.toISOString().slice(0, 10)
}

let settleDay: string | null = null
let lastResult = ''
let checkedAt = 0

/** null = trade as usual; otherwise a line explaining the pause. */
export async function settlePause(now = new Date()): Promise<string | null> {
  if (!settlePauseOn || !inSettlePause(now, HOUR, settleDay)) return null
  if (Date.now() - checkedAt >= CHECK_MS) {
    checkedAt = Date.now()
    try {
      const r = await fetch(`${URL_}/rest/v1/rpc/vault_settle_status`, {
        method: 'POST', headers: { apikey: KEY!, Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' }, body: '{}',
        signal: AbortSignal.timeout(15_000),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const j = await r.json()
      settleDay = j?.settle_day ?? null
      lastResult = j?.last_result ?? ''
      if (!inSettlePause(now, HOUR, settleDay)) return null
    } catch (e: any) {
      return `settlement pause: cannot read the fund's status (${e.message}); no new trades until it settles`
    }
  }
  return `settlement pause: waiting for the fund's daily settlement${lastResult ? ` (keeper: ${lastResult})` : ''}; no new trades`
}
