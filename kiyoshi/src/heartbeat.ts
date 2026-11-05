// Midas's addition: report in to the fund's alert service every few minutes, so Telegram alerts fire if the bot
// stops or keeps failing. The report is signed with the bot wallet key (an off-chain signature, not a
// transaction), so nobody else can fake it. Never throws. Off unless FUND_SUPABASE_URL and FUND_SUPABASE_KEY are set.
import { account } from './wallet.js'

const URL_ = process.env.FUND_SUPABASE_URL
const KEY = process.env.FUND_SUPABASE_KEY
const EVERY_MS = Number(process.env.HEARTBEAT_SEC ?? '300') * 1000

let errorStreak = 0
let sentAt = 0

/** Call once per cycle with the desk's status. */
export async function heartbeat(status: { equity?: number | null; lastError?: { message: string } | null; settlePause?: string | null; holding?: boolean; gas?: { celo: number | null; lastTopUp: unknown; lastError: string | null }; skipSummary?: string; smartSummary?: string; smartSchedule?: string }) {
  errorStreak = status.lastError ? errorStreak + 1 : 0
  if (!URL_ || !KEY || Date.now() - sentAt < EVERY_MS) return
  sentAt = Date.now()
  try {
    const message = JSON.stringify({
      kind: 'kiyoshi-heartbeat', at: new Date().toISOString(), equity: status.equity ?? null,
      error: status.lastError?.message?.slice(0, 300) ?? null, errorStreak, pause: status.settlePause ?? null, holding: !!status.holding,
      celo: status.gas?.celo ?? null, lastTopUp: status.gas?.lastTopUp ?? null, gasError: status.gas?.lastError ?? null, skipSummary: status.skipSummary ?? null, smart: status.smartSummary ?? null, schedule: status.smartSchedule ?? null,
    })
    const signature = await account.signMessage({ message })
    const r = await fetch(`${URL_}/functions/v1/fund-alerts`, {
      method: 'POST', headers: { apikey: KEY, Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ heartbeat: { message, signature } }), signal: AbortSignal.timeout(20_000),
    })
    const j: any = await r.json().catch(() => ({}))
    if (j.error) console.log(`  heartbeat: ${j.error}`)
  } catch (e: any) {
    console.log(`  heartbeat not sent: ${e.message}`)
  }
}
