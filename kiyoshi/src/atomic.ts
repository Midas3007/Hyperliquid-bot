// Atomic Textile-first trading through KiyoshiArb (contracts/KiyoshiArb.sol), switched from Telegram (/atomic_on|off).
//
// The contract holds a USDT float (bot_settings.arb_float_usd, $300 to start). A fast watcher, separate from the main
// 60-second loop, looks for Textile-first gaps every couple of seconds, at several sizes, and fills the best one in
// ONE transaction that reverts unless it ends in profit. Being faster than before:
//   - it polls every ATOMIC_POLL_MS (2 s with the Textile partner key, 4 s without) instead of every 60 s, quoting
//     only the Uniswap fee tier each pool actually uses;
//   - every attempt is simulated first (no gas spent when the gap has already gone) and then sent with a priority fee
//     (PRIORITY_GWEI, 25 gwei by default: about $0.001 a try) so it lands ahead of bots paying the minimum.
// The main loop calls manageFloat() each cycle: it tops the float up from the bot wallet, sends profit above the target
// back, and while atomic is off or the daily settlement is due it returns everything and pauses the contract. The
// contract can only ever send funds to the bot wallet.
import { encodeFunctionData, erc20Abi, formatUnits, parseAbi, parseEventLogs, parseGwei, parseUnits, type Address, type Hex } from 'viem'
import { account, pub, sendTaggedReceipt } from './wallet.js'
import { cancelRfq, preview, requestQuote, submitTx, textileKeyed } from './textile.js'
import { TEXTILE_REACTOR, TOKENS, type TokenKey } from './config.js'
import { uniQuote } from './uniswap.js'
import { entryMin, execMin, floatAction, sizesFor } from './atomic-math.js'

export const ARB = (process.env.ARB_CONTRACT ?? '0x16c790305ecf31ed639d9390a37f4b1386fd8cfb') as Address
export const ATOMIC_CORRIDORS: TokenKey[] = ['wARS', 'cNGN']
const POLL_MS = Number(process.env.ATOMIC_POLL_MS ?? (textileKeyed ? '2000' : '4000'))
const PRIORITY_GWEI = process.env.PRIORITY_GWEI ?? '25'
const ENTRY_BPS = Number(process.env.ATOMIC_MIN_EDGE_BPS ?? '1')
const GAS_LIMIT = 700_000n

const arbAbi = parseAbi([
  'function textileThenUniswap(bytes textileCall, uint256 takerPays, address token, uint24 fee, uint256 minProfit) returns (uint256)',
  'function withdraw(address token, uint256 amount)',
  'function setPaused(bool p)',
  'function paused() view returns (bool)',
  'event Trade(address indexed token, uint256 usdtIn, uint256 tokenGot, uint256 usdtOut, uint256 profit)',
])

const usd = (v: bigint) => Number(formatUnits(v, 6))
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export const atomic = {
  allowed: false, // set by the main loop: switched on, live, not in the settlement pause
  stats: { day: '', tries: 0, fills: 0, profit: 0, noQuote: 0, gone: 0, reverted: 0 },
  lastError: null as string | null,
}
let logEntry: (e: Record<string, unknown>) => void = () => {}

function today() {
  const d = new Date(Date.now() + 3_600_000).toISOString().slice(0, 10) // Lagos day
  if (atomic.stats.day !== d) atomic.stats = { day: d, tries: 0, fills: 0, profit: 0, noQuote: 0, gone: 0, reverted: 0 }
  return atomic.stats
}
export function atomicSummary(on: boolean, floatUsd: number) {
  const s = today()
  return on ? `atomic ON (float $${floatUsd.toFixed(0)}; today ${s.fills} fills +$${s.profit.toFixed(2)}, ${s.tries} tries, ${s.gone} gaps gone before sending, ${s.reverted} reverted${textileKeyed ? '' : '; no Textile key'})` : 'atomic OFF'
}

const bal = (token: Address, who: Address) => pub.readContract({ address: token, abi: erc20Abi, functionName: 'balanceOf', args: [who] })
const arbCall = (functionName: 'withdraw' | 'setPaused', args: readonly unknown[]) =>
  sendTaggedReceipt({ to: ARB, data: encodeFunctionData({ abi: arbAbi, functionName, args: args as any }) })

/** The contract's holdings, for the fund's value: USDT plus any corridor tokens (normally none between trades). */
export async function arbHoldings(): Promise<{ token: TokenKey; amount: bigint }[]> {
  const out: { token: TokenKey; amount: bigint }[] = []
  for (const k of ['USDT', ...ATOMIC_CORRIDORS] as TokenKey[]) {
    const a = await bal(TOKENS[k].address, ARB)
    if (a > 0n) out.push({ token: k, amount: a })
  }
  return out
}

/** Called by the main loop every cycle. Returns a line for the log when it moved anything. */
export async function manageFloat(on: boolean, floatUsd: number): Promise<string | null> {
  const paused = await pub.readContract({ address: ARB, abi: arbAbi, functionName: 'paused' })
  if (!on) {
    const moved: string[] = []
    for (const h of await arbHoldings()) {
      await arbCall('withdraw', [TOKENS[h.token].address, 2n ** 256n - 1n])
      moved.push(`${h.token === 'USDT' ? '$' + usd(h.amount).toFixed(2) : h.amount.toString() + ' ' + h.token}`)
    }
    if (!paused) await arbCall('setPaused', [true])
    return moved.length ? `atomic: returned ${moved.join(', ')} from the contract to the bot wallet${paused ? '' : ' and paused it'}` : (paused ? null : 'atomic: contract paused')
  }
  const notes: string[] = []
  if (paused) { await arbCall('setPaused', [false]); notes.push('contract unpaused') }
  const target = parseUnits(Math.max(0, floatUsd).toFixed(2), 6)
  const a = floatAction(await bal(TOKENS.USDT.address, ARB), target, await bal(TOKENS.USDT.address, account.address))
  if (a?.kind === 'top-up') {
    await sendTaggedReceipt({ to: TOKENS.USDT.address, data: encodeFunctionData({ abi: erc20Abi, functionName: 'transfer', args: [ARB, a.amount] }) })
    notes.push(`float topped up with $${usd(a.amount).toFixed(2)}`)
  } else if (a?.kind === 'skim') {
    await arbCall('withdraw', [TOKENS.USDT.address, a.amount])
    notes.push(`$${usd(a.amount).toFixed(2)} of profit returned to the bot wallet`)
  }
  return notes.length ? 'atomic: ' + notes.join('; ') : null
}

// ---------- the fast watcher ----------
const tier = new Map<TokenKey, { fee: number; at: number }>()
async function poolFee(X: TokenKey, probe: bigint) {
  const t = tier.get(X)
  if (t && Date.now() - t.at < 10 * 60_000) return t.fee
  const q = await uniQuote(X, 'USDT', probe)
  if (q) tier.set(X, { fee: q.fee, at: Date.now() })
  return q?.fee ?? null
}

type Cand = { X: TokenKey; size: bigint; fee: number; edge: bigint }

async function scan(float: bigint): Promise<Cand | null> {
  let best: Cand | null = null
  for (const X of ATOMIC_CORRIDORS) {
    for (const size of sizesFor(float)) {
      let p: any
      try { p = await preview({ sellToken: TOKENS.USDT.address, buyToken: TOKENS[X].address, sellAmount: size.toString() }) } catch { continue }
      if (p?.status !== 'preview') continue
      const got = BigInt(p.buyAmount), pays = BigInt(p.takerPays ?? size)
      const fee = await poolFee(X, got)
      if (fee === null) continue
      const q = await uniQuote(X, 'USDT', got, [fee])
      if (!q) continue
      const edge = q.out - pays
      if (!best || edge > best.edge) best = { X, size, fee, edge }
    }
  }
  return best
}

async function attempt(c: Cand) {
  const s = today()
  s.tries++
  const q: any = await requestQuote({ sellToken: TOKENS.USDT.address, buyToken: TOKENS[c.X].address, sellAmount: c.size.toString() }, ARB)
  if (q.status === 'no_quote') { s.noQuote++; return }
  const { rfqId, claimToken, quote, transactions } = q
  if (String(quote.reactor).toLowerCase() !== TEXTILE_REACTOR.toLowerCase() || String(transactions.swap.to).toLowerCase() !== TEXTILE_REACTOR.toLowerCase()) throw new Error(`unexpected reactor ${quote.reactor}`)
  if (String(quote.taker).toLowerCase() !== ARB.toLowerCase()) throw new Error(`quote is for ${quote.taker}, not the contract`)
  if (Date.now() >= Date.parse(quote.expiresAt) - 3_000) { s.gone++; return }
  const got = BigInt(quote.buyAmount), pays = BigInt(quote.takerPays)
  const u = await uniQuote(c.X, 'USDT', got, [c.fee])
  const expected = u ? u.out - pays : -1n
  if (!u || expected < entryMin(c.size, ENTRY_BPS)) { s.gone++; await cancelRfq(rfqId, claimToken); return }
  const data = encodeFunctionData({ abi: arbAbi, functionName: 'textileThenUniswap', args: [transactions.swap.data as Hex, pays, TOKENS[c.X].address, c.fee, execMin(c.size)] })
  // Simulate first: if the gap has already gone, nothing is sent and no gas is spent
  try { await pub.call({ account: account.address, to: ARB, data, gas: GAS_LIMIT }) } catch { s.gone++; await cancelRfq(rfqId, claimToken); return }
  const fees = await pub.estimateFeesPerGas()
  const prio = parseGwei(PRIORITY_GWEI)
  let receipt
  try {
    receipt = await sendTaggedReceipt({ to: ARB, data }, { gas: GAS_LIMIT, maxPriorityFeePerGas: prio, maxFeePerGas: fees.maxFeePerGas + prio })
  } catch (e: any) {
    s.reverted++
    logEntry({ event: 'atomic', X: c.X, sizeUsd: usd(c.size), result: 'reverted', expectedUsd: usd(expected), error: String(e.shortMessage ?? e.message).split('\n')[0].slice(0, 160) })
    return
  }
  const ev = parseEventLogs({ abi: arbAbi, logs: receipt.logs, eventName: 'Trade' })[0]
  const profit = ev ? usd(ev.args.profit) : 0
  s.fills++; s.profit += profit
  await submitTx(rfqId, claimToken, receipt.transactionHash).catch(() => {})
  console.log(`  atomic: ${c.X} $${usd(c.size).toFixed(2)} filled in one transaction, +$${profit.toFixed(4)} (expected +$${usd(expected).toFixed(4)})`)
  logEntry({ event: 'cycle', X: c.X, route: 'atomic', sizeUsd: usd(pays), tx1: receipt.transactionHash, textileUsd: usd(pays), pnl: profit, expectedUsd: usd(expected) })
}

let running = false
/** Starts the watcher once; `log` writes to the bot's trade ledger. */
export function startAtomicWatcher(log: (e: Record<string, unknown>) => void) {
  if (running) return
  running = true
  logEntry = log
  ;(async () => {
    while (true) {
      try {
        if (atomic.allowed) {
          const float = await bal(TOKENS.USDT.address, ARB)
          const c = float >= 20_000000n ? await scan(float) : null
          if (c && c.edge >= entryMin(c.size, ENTRY_BPS)) await attempt(c)
          atomic.lastError = null
        }
      } catch (e: any) {
        atomic.lastError = String(e.shortMessage ?? e.message).split('\n')[0].slice(0, 200)
        console.error('  atomic error:', atomic.lastError)
      }
      await sleep(POLL_MS)
    }
  })()
}
