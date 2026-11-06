// Read-only: one Lagos day's round trips and realized P&L for a wallet.
// Usage: npx tsx scripts/day-stats.ts <address> <YYYY-MM-DD>
import { createPublicClient, formatUnits, http, parseAbiItem, type Address, type Hex } from 'viem'
import { celo } from 'viem/chains'
import { RPC_URL, TEXTILE_REACTOR, TOKENS, UNI, type TokenKey } from '../src/config.js'
const pub = createPublicClient({ chain: celo, transport: http(RPC_URL) }) // no key needed
const who = (process.argv[2] ?? '').toLowerCase() as Address, day = process.argv[3]
const transfer = parseAbiItem('event Transfer(address indexed from, address indexed to, uint256 value)')
const usd = (v: bigint) => Number(formatUnits(v, 6))
async function retry<T>(fn: () => Promise<T>): Promise<T> { for (let i = 0; ; i++) { try { return await fn() } catch (e) { if (i >= 5) throw e; await new Promise((r) => setTimeout(r, 1500)) } } }
const headN = await retry(() => pub.getBlockNumber()), head = await retry(() => pub.getBlock({ blockNumber: headN }))
const t0 = Date.parse(day + 'T00:00:00+01:00') / 1000, t1 = t0 + 86400
const blockAt = (t: number) => headN - BigInt(Math.round(Number(head.timestamp) - t))
const s = blockAt(t0), e = blockAt(t1) > headN ? headN : blockAt(t1), from = s - 1800n
const keys = Object.keys(TOKENS) as TokenKey[]
const perTx = new Map<Hex, { d: Partial<Record<TokenKey, bigint>>; block: bigint }>()
for (const k of keys) for (const dir of ['from', 'to'] as const) for (let a = from; a <= e; a += 2000n) {
  const b = a + 1999n > e ? e : a + 1999n
  const logs = await retry(() => pub.getLogs({ address: TOKENS[k].address, event: transfer, args: { [dir]: who } as any, fromBlock: a, toBlock: b }))
  for (const l of logs) { const x = perTx.get(l.transactionHash) ?? { d: {}, block: l.blockNumber! }; x.d[k] = (x.d[k] ?? 0n) + (dir === 'to' ? 1n : -1n) * (l.args.value as bigint); perTx.set(l.transactionHash, x) }
}
type Leg = { block: bigint; venue: string; X: TokenKey; usdt: bigint; x: bigint }
const legs: Leg[] = []
for (const [hash, { d, block }] of perTx) {
  const X = keys.find((k) => k !== 'USDT' && k !== 'USDC' && (d[k] ?? 0n) !== 0n); if (!X || !d.USDT) continue
  const tx = await retry(() => pub.getTransaction({ hash })); if (tx.from.toLowerCase() !== who) continue
  const to = tx.to?.toLowerCase(), venue = to === UNI.ROUTER.toLowerCase() ? 'uniswap' : to === TEXTILE_REACTOR.toLowerCase() ? 'textile' : null
  if (venue) legs.push({ block, venue, X, usdt: d.USDT, x: d[X]! })
}
legs.sort((a, b) => (a.block < b.block ? -1 : 1))
const close = (a: bigint, b: bigint) => { const m = a > b ? a : b, diff = a > b ? a - b : b - a; return diff * 100n <= m }
const open: Leg[] = []; let trips = 0, wins = 0, pnl = 0, vol = 0, maxBuy = 0; const hours = new Array(24).fill(0)
for (const l of legs) {
  if (l.usdt < 0n && l.x > 0n) { open.push(l); continue }
  const i = open.findIndex((o) => o.X === l.X && o.venue !== l.venue && close(o.x, -l.x)); if (i < 0) continue
  const o = open.splice(i, 1)[0]; if (l.block < s) continue
  const p = usd(l.usdt) - usd(-o.usdt); trips++; pnl += p; if (p > 0) wins++; vol += usd(-o.usdt); maxBuy = Math.max(maxBuy, usd(-o.usdt))
  hours[new Date((t0 + Number(l.block - s)) * 1000 + 3600_000).getUTCHours()] += p
}
let acc = 0, tTrips = 0, tWins = 0, tPnl = 0, tVol = 0, unmatchedLoss = 0
for (const l of legs) { const d = usd(l.usdt); if (d < 0) { acc += -d; continue } if (acc <= 0) continue
  if (l.block >= s) { const p = d - acc; tTrips++; tPnl += p; tVol += acc; if (p > 0) tWins++ } acc = 0 }
// The main numbers use the same method as Telegram's /today (every buy counts until the next sale, losses included).
// The matched-only figures leave out trades whose sale didn't match the buy (usually the losing ones), so they read high.
console.log(JSON.stringify({ who, day, trips: tTrips, wins: tWins, pnl: +tPnl.toFixed(2), traded: +tVol.toFixed(2) }))
console.log(JSON.stringify({ matchedOnly: { trips, wins, pnl: +pnl.toFixed(2), avgTrade: +(vol / (trips || 1)).toFixed(0), maxBuy: +maxBuy.toFixed(0), perTrip: +(pnl / (trips || 1)).toFixed(4), hours: hours.map((h) => +h.toFixed(2)) } }))
