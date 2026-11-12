// Reads the pool wallet's USDT movements straight from a Celo node (JSON-RPC) and keeps them in the database,
// so the trade history never has to be downloaded again in full. No Deno-only code here: the same file runs
// under Node for testing against the real node.
//
// Rules this file keeps (each one exists because breaking it showed wrong numbers once, or would):
//  - An answer only counts when it has the expected shape. Anything else is an error, never "nothing happened".
//  - A block range only counts as read when the node proves it has the last block of the range.
//  - The cursor only moves after the rows of that range are stored (one database transaction per range).

export const USDT = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
const TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
export const SPAN = 5000 // the node refuses log queries over more than 5000 blocks
export const MARGIN = 12 // stay this many blocks (seconds) behind the head
export const OVERLAP = 1800 // every run re-reads the last 30 minutes, so anything that moved is corrected
export const AUDIT_BLOCKS = 86_400 // once a day a run re-reads the last 24 hours
export const AUDIT_EVERY_MS = 24 * 3600_000
const TX_BATCH = 40 // transactions looked up per request

export type Call = { method: string; params: unknown[] }
export type Rpc = (calls: Call[]) => Promise<any[]>
export type Row = { hash: string; block: number; tx_index: number; t: number; sent: boolean; to: string; usdt: string }
export type Known = Map<string, { sent: boolean; to: string }>
export type State = { from_block: number; block: number; since_ms: number; synced_at: string | null; audited_at: string | null }
export type Store = {
  load(wallet: string): Promise<State | null>
  /** Start (or restart) the history at fromBlock: the cursor goes to fromBlock - 1. */
  init(wallet: string, fromBlock: number, sinceMs: number): Promise<void>
  /** What is already stored for these blocks, so known transactions are not looked up again. */
  known(wallet: string, lo: number, hi: number): Promise<Known>
  /** Store the rows of blocks lo..hi and move the cursor to hi, atomically. */
  apply(wallet: string, lo: number, hi: number, hiTimeMs: number, rows: Row[], replace: boolean): Promise<void>
  /** The cursor reached the head. */
  reached(wallet: string, atIso: string, audited: boolean): Promise<void>
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const hex = (n: number) => '0x' + n.toString(16)
const isHash = (x: unknown): x is string => typeof x === 'string' && /^0x[0-9a-fA-F]{64}$/.test(x)
const isAddr = (x: unknown): x is string => typeof x === 'string' && /^0x[0-9a-fA-F]{40}$/.test(x)
/** A hex quantity as a safe whole number, or an error. */
function num(x: unknown, what: string): number {
  if (typeof x !== 'string' || !/^0x[0-9a-fA-F]+$/.test(x)) throw new Error(`node sent a bad ${what}`)
  const n = Number(x)
  if (!Number.isSafeInteger(n) || n < 0) throw new Error(`node sent a bad ${what}`)
  return n
}

/**
 * JSON-RPC over HTTP, one batch per request. Resolves to the results in the order asked, or throws.
 * Retries a rate limit, a server error, a network error or an unreadable answer (0.5s, 1s, 2s apart).
 */
export function makeRpc(url: string, timeoutMs = 15_000): Rpc {
  return async (calls) => {
    const body = calls.map((c, i) => ({ jsonrpc: '2.0', id: i + 1, method: c.method, params: c.params }))
    let wait = 500
    let last = 'no answer'
    for (let attempt = 0; attempt < 4; attempt++) {
      if (attempt > 0) { await sleep(wait); wait *= 2 }
      let r: Response
      try {
        r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) })
      } catch (e) {
        last = `network: ${(e as Error).message}`
        continue
      }
      if (r.status === 429 || r.status >= 500) { last = `node returned ${r.status}`; await r.body?.cancel().catch(() => {}); continue }
      const j: any = await r.json().catch(() => null)
      const list: any[] | null = Array.isArray(j) ? j : null
      if (!list) {
        last = `node said: ${String(j?.error?.message ?? `unreadable answer (${r.status})`).slice(0, 120)}`
        if (r.status >= 400 && r.status < 500) throw new Error(last) // the request itself is wrong: asking again cannot help
        continue
      }
      const byId = new Map<number, any>(list.map((x) => [x?.id, x]))
      const out: any[] = []
      let bad: string | null = null
      for (const b of body) {
        const x = byId.get(b.id)
        if (!x || x.error || !('result' in x)) { bad = `node said: ${String(x?.error?.message ?? 'an answer is missing').slice(0, 120)} (${b.method})`; break }
        out.push(x.result)
      }
      if (!bad) return out
      last = bad
    }
    throw new Error(last)
  }
}

/** Micro-USDT (6 decimals) as a plain decimal string, exact. */
export function microToDecimal(v: bigint): string {
  const neg = v < 0n
  const a = neg ? -v : v
  return `${neg ? '-' : ''}${a / 1_000_000n}.${(a % 1_000_000n).toString().padStart(6, '0')}`
}

/**
 * Every transaction in blocks lo..hi that moved the wallet's USDT, with the net change, whether the wallet
 * itself sent it, and what it called. Also the timestamp of block hi. Throws unless the node has block hi.
 */
export async function readWindow(rpc: Rpc, wallet: string, lo: number, hi: number, known: Known): Promise<{ rows: Row[]; hiTimeMs: number }> {
  const w = wallet.toLowerCase()
  const topicW = '0x' + '0'.repeat(24) + w.slice(2)
  const range = { address: USDT, fromBlock: hex(lo), toBlock: hex(hi) }
  const [blk, out, inn] = await rpc([
    { method: 'eth_getBlockByNumber', params: [hex(hi), false] },
    { method: 'eth_getLogs', params: [{ ...range, topics: [TRANSFER, topicW] }] },
    { method: 'eth_getLogs', params: [{ ...range, topics: [TRANSFER, null, topicW] }] },
  ])
  if (!blk || num(blk.number, 'block number') !== hi) throw new Error(`node does not have block ${hi} yet`)
  const hiTimeMs = num(blk.timestamp, 'block time') * 1000
  if (!Array.isArray(out) || !Array.isArray(inn)) throw new Error('node sent no list of transfers')

  // One entry per log (a transfer from the wallet to itself is in both lists), then the net change per transaction.
  const logs = new Map<string, any>()
  for (const l of [...out, ...inn]) {
    if (l?.removed === true) continue
    if (!isHash(l?.transactionHash) || !Array.isArray(l?.topics) || l.topics.length !== 3 || String(l.address).toLowerCase() !== USDT) throw new Error('node sent a malformed transfer')
    logs.set(`${l.transactionHash.toLowerCase()}:${num(l.logIndex, 'log index')}`, l)
  }
  type Acc = { block: number; tx_index: number; ts: number | null; micro: bigint }
  const perTx = new Map<string, Acc>()
  for (const l of logs.values()) {
    const hash = l.transactionHash.toLowerCase()
    const block = num(l.blockNumber, 'block number')
    if (block < lo || block > hi) throw new Error('node sent a transfer outside the range asked')
    const from = '0x' + String(l.topics[1]).slice(26).toLowerCase()
    const to = '0x' + String(l.topics[2]).slice(26).toLowerCase()
    if (typeof l.data !== 'string' || !/^0x[0-9a-fA-F]{64}$/.test(l.data)) throw new Error('node sent a malformed transfer amount')
    const sign = to === w ? 1n : from === w ? -1n : 0n
    if (sign === 0n) throw new Error('node sent a transfer that is not the wallet\'s')
    const e = perTx.get(hash) ?? { block, tx_index: num(l.transactionIndex, 'transaction index'), ts: l.blockTimestamp == null ? null : num(l.blockTimestamp, 'block time'), micro: 0n }
    if (e.block !== block) throw new Error('node sent one transaction in two blocks')
    e.micro += sign * BigInt(l.data)
    perTx.set(hash, e)
  }

  // Block times, for nodes that do not put them on the log.
  const needTime = [...new Set([...perTx.values()].filter((e) => e.ts == null).map((e) => e.block))]
  const timeOf = new Map<number, number>()
  for (let i = 0; i < needTime.length; i += TX_BATCH) {
    const part = needTime.slice(i, i + TX_BATCH)
    const blocks = await rpc(part.map((b) => ({ method: 'eth_getBlockByNumber', params: [hex(b), false] })))
    part.forEach((b, k) => {
      if (!blocks[k] || num(blocks[k].number, 'block number') !== b) throw new Error(`node does not have block ${b}`)
      timeOf.set(b, num(blocks[k].timestamp, 'block time'))
    })
  }

  // Who sent each transaction and what it called: asked once per transaction, then remembered.
  const fresh = [...perTx.keys()].filter((h) => !known.has(h))
  const who = new Map<string, { sent: boolean; to: string }>()
  for (let i = 0; i < fresh.length; i += TX_BATCH) {
    const part = fresh.slice(i, i + TX_BATCH)
    const txs = await rpc(part.map((h) => ({ method: 'eth_getTransactionByHash', params: [h] })))
    part.forEach((h, k) => {
      const tx = txs[k]
      if (!tx || String(tx.hash).toLowerCase() !== h || !isAddr(tx.from)) throw new Error(`node does not have transaction ${h.slice(0, 12)}`)
      if (num(tx.blockNumber, 'block number') !== perTx.get(h)!.block) throw new Error(`transaction ${h.slice(0, 12)} moved to another block`)
      if (tx.to != null && !isAddr(tx.to)) throw new Error('node sent a malformed transaction')
      who.set(h, { sent: tx.from.toLowerCase() === w, to: tx.to == null ? '' : tx.to.toLowerCase() })
    })
  }

  const rows: Row[] = []
  for (const [hash, e] of perTx) {
    const k = known.get(hash) ?? who.get(hash)!
    const ts = e.ts ?? timeOf.get(e.block)!
    rows.push({ hash, block: e.block, tx_index: e.tx_index, t: ts * 1000, sent: k.sent, to: k.to, usdt: microToDecimal(e.micro) })
  }
  rows.sort((x, y) => x.block - y.block || x.tx_index - y.tx_index)
  return { rows, hiTimeMs }
}

/** The last block at or before a time. Celo makes one block a second; the answer is checked against the node. */
export async function blockAtTime(rpc: Rpc, head: number, ms: number): Promise<number> {
  const want = Math.floor(ms / 1000)
  const [hb] = await rpc([{ method: 'eth_getBlockByNumber', params: [hex(head), false] }])
  if (!hb) throw new Error('node does not have its own head block')
  let n = Math.min(head, Math.max(0, head - (num(hb.timestamp, 'block time') - want)))
  for (let i = 0; i < 12; i++) {
    const [b] = await rpc([{ method: 'eth_getBlockByNumber', params: [hex(n), false] }])
    if (!b) throw new Error(`node does not have block ${n}`)
    const ts = num(b.timestamp, 'block time')
    if (ts <= want || n === 0) return n
    n = Math.max(0, n - Math.max(1, ts - want))
  }
  throw new Error('could not find the block for the start of the season')
}

export type SyncResult = { reached: boolean; cursor: number; head: number; windows: number; rows: number }

/**
 * Bring the stored history up to the head (minus MARGIN), within a time budget. Safe to run at any moment and
 * from several places at once: every step is idempotent, and whatever is not finished is picked up next time.
 */
export async function syncChain(
  rpc: Rpc,
  store: Store,
  wallet: string,
  sinceMs: number,
  opt: { budgetMs: number; audit: boolean; now?: () => number },
): Promise<SyncResult> {
  const now = opt.now ?? Date.now
  const t0 = now()
  const [headHex] = await rpc([{ method: 'eth_blockNumber', params: [] }])
  const head = num(headHex, 'head block')
  const target = head - MARGIN

  let st = await store.load(wallet)
  // First run for this wallet, or the season now starts earlier than what is stored: (re)start from the season start.
  if (!st || sinceMs < st.since_ms - 1000) {
    const from = Math.max(0, (await blockAtTime(rpc, head, sinceMs)) - 60)
    await store.init(wallet, from, sinceMs)
    st = { from_block: from, block: from - 1, since_ms: sinceMs, synced_at: null, audited_at: null }
  }

  const backfilling = st.synced_at == null
  const auditDue = opt.audit && !backfilling && (st.audited_at == null || now() - Date.parse(st.audited_at) > AUDIT_EVERY_MS)
  const overlap = backfilling ? 0 : auditDue ? AUDIT_BLOCKS : OVERLAP
  const startCursor = st.block
  let a = Math.max(st.from_block, startCursor + 1 - overlap)
  let windows = 0
  let rowCount = 0
  while (a <= target) {
    if (windows > 0 && now() - t0 > opt.budgetMs) break
    const b = Math.min(a + SPAN - 1, target)
    const known = await store.known(wallet, a, b)
    const { rows, hiTimeMs } = await readWindow(rpc, wallet, a, b, known)
    // In the recent zone the node's answer replaces what is stored (a transaction that vanished in a chain
    // reorganisation is removed). Older blocks are only ever added to, so a bad answer cannot erase history.
    const replace = !backfilling && a >= startCursor + 1 - OVERLAP
    await store.apply(wallet, a, b, hiTimeMs, rows, replace)
    windows++
    rowCount += rows.length
    a = b + 1
  }
  const reached = a > target
  if (reached) await store.reached(wallet, new Date(now()).toISOString(), backfilling || auditDue)
  return { reached, cursor: Math.max(startCursor, a - 1), head, windows, rows: rowCount }
}

/** The trade pairing, as the dashboard and /today have always counted it. Kept here for tests; the database
 *  function pool_trades does the same thing in SQL. */
export function pairTrades(rows: Row[], sinceMs: number): { t: number; size: number; profit: number }[] {
  const trades: { t: number; size: number; profit: number }[] = []
  let open = 0n
  const micro = (s: string) => { const neg = s.startsWith('-'); const [i, f = ''] = s.replace('-', '').split('.'); const v = BigInt(i) * 1_000_000n + BigInt((f + '000000').slice(0, 6)); return neg ? -v : v }
  for (const r of [...rows].sort((x, y) => x.block - y.block || x.tx_index - y.tx_index)) {
    if (!r.sent || r.t < sinceMs) continue
    const d = micro(r.usdt)
    if (d === 0n || r.to === USDT) continue // no USDT moved, or a direct USDT transfer (a withdrawal)
    if (d < 0n) open += -d
    else if (open > 0n) { trades.push({ t: r.t, size: Number(open) / 1e6, profit: Number(d - open) / 1e6 }); open = 0n }
  }
  return trades
}
