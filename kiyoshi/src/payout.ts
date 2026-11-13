// Instant treasury withdrawal: Midas asks on the fund site's admin page; the bot pays it here on its next cycle.
// The limits are checked again in this file, with their own record on disk, so even if the site or database were
// broken into, the most that could leave the wallet is $10 per 30 days.
// Off unless FUND_SUPABASE_URL and FUND_SUPABASE_KEY are set.
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { encodeFunctionData, erc20Abi, isAddress, parseUnits, type Address } from 'viem'
import { account, sendTagged } from './wallet.js'
import { balanceOf } from './swap.js'
import { TEXTILE_REACTOR, TOKENS, UNI } from './config.js'

const URL_ = process.env.FUND_SUPABASE_URL
const KEY = process.env.FUND_SUPABASE_KEY
const FILE = (process.env.DATA_DIR ?? '.') + '/payouts.json'
const MIN_USD = 5
const MAX_USD = 10
const GAP_MS = 30 * 86_400_000
const CHECK_MS = 2 * 60_000
let checkedAt = 0

type Paid = { id: number; at: number; amount: number; dest: string; tx?: string }
function read(): Paid[] {
  if (!existsSync(FILE)) return []
  try { return JSON.parse(readFileSync(FILE, 'utf8')) } catch { throw new Error('payouts.json is unreadable: refusing to pay') }
}

/** Pays the waiting withdrawal, if there is one and every limit allows it. Returns a line for the log, or null. */
export async function payout(): Promise<string | null> {
  if (!URL_ || !KEY || Date.now() - checkedAt < CHECK_MS) return null
  checkedAt = Date.now()
  const r = await fetch(`${URL_}/rest/v1/rpc/treasury_payout`, {
    method: 'POST', headers: { apikey: KEY, Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' }, body: '{}', signal: AbortSignal.timeout(10_000),
  })
  if (!r.ok) return null // the treasury is not set up, or the database is unreachable
  const q: any = await r.json()
  if (!q || typeof q !== 'object') return null
  const id = Number(q.id), amount = Number(q.amount), dest = String(q.dest ?? '').toLowerCase()
  const paid = read()
  if (!Number.isInteger(id) || paid.some((p) => p.id === id)) return null // nothing waiting, or already handled
  const refuse = (why: string) => `treasury withdrawal #${id} refused: ${why}`
  if (!(amount >= MIN_USD && amount <= MAX_USD)) return refuse(`$${amount} is outside $${MIN_USD}-$${MAX_USD}`)
  const banned = [account.address, TOKENS.USDT.address, TEXTILE_REACTOR, UNI.ROUTER, '0x0000000000000000000000000000000000000000'].map((a) => a.toLowerCase())
  if (!isAddress(dest) || banned.includes(dest)) return refuse('bad address')
  const last = Math.max(0, ...paid.map((p) => p.at))
  if (Date.now() - last < GAP_MS) return refuse('one withdrawal per 30 days')
  const units = parseUnits(amount.toFixed(2), 6)
  if ((await balanceOf(TOKENS.USDT.address)) < units) return null // the cash is in a trade right now: try next time
  // Record first: if anything fails after this point the withdrawal is NOT retried (better unpaid than paid twice).
  const entry: Paid = { id, at: Date.now(), amount, dest }
  writeFileSync(FILE, JSON.stringify([...paid, entry]))
  const tx = await sendTagged({ to: TOKENS.USDT.address, data: encodeFunctionData({ abi: erc20Abi, functionName: 'transfer', args: [dest as Address, units] }) })
  entry.tx = tx
  writeFileSync(FILE, JSON.stringify([...paid, entry]))
  return `treasury withdrawal #${id}: $${amount.toFixed(2)} sent to ${dest} (${tx})`
}
