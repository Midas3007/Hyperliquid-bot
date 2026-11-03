// Midas's addition: skip a trade when Textile still reserves our funds for an earlier quote, instead of waiting up
// to two minutes and then trading on stale prices. Applies to the first leg of a Textile -> Uniswap trade (nothing
// is bought yet). On by default; Midas switches it from Telegram (/skipwait_off, /skipwait_on), which stores the
// setting in Supabase; the bot reads it every 2 minutes through the public bot_settings() call and stays on if it
// cannot. A tally of skips and of trades that did wait (when it is off) is kept in DATA_DIR across restarts.
import { existsSync, readFileSync, writeFileSync } from 'node:fs'

const URL_ = process.env.FUND_SUPABASE_URL
const KEY = process.env.FUND_SUPABASE_KEY
const CHECK_MS = 2 * 60_000
const FILE = (process.env.DATA_DIR ?? '.') + '/skip-reserved.json'

type Tally = { since: string; skipped: number; waited: number; waitedPnl: number; waitedLosers: number }
let tally: Tally = { since: new Date().toISOString(), skipped: 0, waited: 0, waitedPnl: 0, waitedLosers: 0 }
try { if (existsSync(FILE)) tally = { ...tally, ...JSON.parse(readFileSync(FILE, 'utf8')) } } catch { /* start fresh */ }
const save = () => { try { writeFileSync(FILE, JSON.stringify(tally, null, 2)) } catch { /* not fatal */ } }

let on = true
let checkedAt = 0

/** The Telegram setting: true unless explicitly switched off. */
export function settingFrom(j: any) {
  return j?.skip_reserved !== false
}

/** Whether the next Textile -> Uniswap trade may be skipped when Textile reserves our funds. */
export async function skipReserved() {
  if (URL_ && KEY && Date.now() - checkedAt >= CHECK_MS) {
    checkedAt = Date.now()
    try {
      const r = await fetch(`${URL_}/rest/v1/rpc/bot_settings`, {
        method: 'POST', headers: { apikey: KEY, Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' }, body: '{}',
        signal: AbortSignal.timeout(10_000),
      })
      if (r.ok) {
        const next = settingFrom(await r.json())
        if (next !== on) console.log(`  skipping trades Textile makes wait for: switched ${next ? 'ON' : 'OFF'} from Telegram`)
        on = next
      }
    } catch { /* keep the last known setting */ }
  }
  return on
}

export function recordSkip() { tally.skipped++; save() }
export function recordWaited(pnl: number) {
  tally.waited++; tally.waitedPnl += pnl; if (pnl < 0) tally.waitedLosers++; save()
}
export const skipSummary = () =>
  `since ${tally.since.slice(0, 10)}: ${tally.skipped} skipped · ${tally.waited} waited, ${tally.waitedPnl >= 0 ? '+' : ''}${tally.waitedPnl.toFixed(4)} USDT, ${tally.waitedLosers} lost`
export const skipTally = () => ({ ...tally, on })
