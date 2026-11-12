// End to end, locally: the real chain.ts reading the real Celo node, storing through the real SQL functions of
// 013_pool_tx.sql (and 014's pool_apply with the peer column) in its own throwaway database `treasury_e2e`.
// Run: NODE_USE_ENV_PROXY=1 NODE_EXTRA_CA_CERTS=/root/.ccr/ca-bundle.crt node e2e.test.mts The store below issues the same statements the edge function's store
// does through supabase-js. Includes a failure in the middle of the first load, and tampering to see self-repair.
import { readFileSync } from 'node:fs'
import { buildDb } from './lib-db.mjs'
import { makeRpc, syncChain, OVERLAP, type Known, type Row, type Rpc, type State, type Store } from '../functions/trade-stats/chain.ts'

const W = '0x6cd37bc290c5c02ea92086d416a6a673bca581dc'
const SINCE = Date.parse('2026-10-29T11:31:32.041174+00:00')
const USDT = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
const db = await buildDb('treasury_e2e', ['009_alerts.sql', '013_pool_tx.sql', '014_treasury.sql'])
let pass = 0, fail = 0
const ok = (c: boolean, name: string, extra = '') => { if (c) { pass++; console.log('  ok   ', name) } else { fail++; console.log('  FAIL ', name, extra) } }

await db.query(`delete from public.pool_tx where wallet = $1`, [W]); await db.query(`delete from public.pool_sync where wallet = $1`, [W])
const store: Store = {
  async load(w) { const r = (await db.query(`select from_block, block, since_ms, synced_at, audited_at from public.pool_sync where wallet = $1`, [w])).rows[0]; return r ? { from_block: Number(r.from_block), block: Number(r.block), since_ms: Number(r.since_ms), synced_at: r.synced_at?.toISOString() ?? null, audited_at: r.audited_at?.toISOString() ?? null } as State : null },
  async init(w, from, since) { await db.query(`insert into public.pool_sync (wallet, from_block, block, block_time, since_ms, synced_at, audited_at, last_error) values ($1,$2,$3,null,$4,null,null,null) on conflict (wallet) do update set from_block = excluded.from_block, block = excluded.block, block_time = null, since_ms = excluded.since_ms, synced_at = null, audited_at = null, last_error = null`, [w, from, from - 1, since]) },
  async known(w, lo, hi) { const r = (await db.query(`select hash, sent, to_addr from public.pool_tx where wallet = $1 and block >= $2 and block <= $3 limit 1000`, [w, lo, hi])).rows; return new Map(r.map((x) => [x.hash, { sent: x.sent === true, to: String(x.to_addr ?? '') }])) as Known },
  async apply(w, lo, hi, hiTime, rows: Row[], replace) { await db.query(`select public.pool_apply($1,$2,$3,$4,$5::jsonb,$6)`, [w, lo, hi, new Date(hiTime).toISOString(), JSON.stringify(rows), replace]) },
  async reached(w, at, audited) { await db.query(`update public.pool_sync set synced_at = $2, last_error = null${audited ? ', audited_at = $2' : ''} where wallet = $1`, [w, at]) },
}
const real = makeRpc('https://forno.celo.org')
let calls = 0, failAt = -1
const rpc: Rpc = async (c) => { calls++; if (calls === failAt) throw new Error('injected failure'); return real(c) }
const st = async () => (await db.query(`select * from public.pool_sync where wallet = $1`, [W])).rows[0]
const count = async () => (await db.query(`select count(*)::int n from public.pool_tx where wallet = $1`, [W])).rows[0].n
const trades = async () => (await db.query(`select public.pool_trades($1,$2) j`, [W, SINCE])).rows[0].j as { t: number; size: number; profit: number }[]

console.log('first load, with a failure injected part-way')
failAt = 25
let threw = ''
try { await syncChain(rpc, store, W, SINCE, { budgetMs: 60_000, audit: true }) } catch (e) { threw = (e as Error).message }
let s = await st()
ok(threw === 'injected failure', 'the failure surfaces as an error', threw)
ok(s && s.synced_at === null && Number(s.block) >= Number(s.from_block) - 1, 'state after the failure: not marked loaded', JSON.stringify(s))
const cursorAfterFail = Number(s.block), rowsAfterFail = await count()
const beyond = (await db.query(`select count(*)::int n from public.pool_tx where wallet = $1 and block > $2`, [W, cursorAfterFail])).rows[0].n
ok(beyond === 0, `nothing stored beyond the cursor (cursor ${cursorAfterFail}, ${rowsAfterFail} rows)`)
failAt = -1
let rounds = 0
for (;;) { const r = await syncChain(rpc, store, W, SINCE, { budgetMs: 25_000, audit: true }); rounds++; if (rounds === 1) ok(r.cursor > cursorAfterFail, 'the next run carries on from the cursor'); if (r.reached) break; if (rounds > 30) throw new Error('never reached') }
s = await st()
ok(s.synced_at !== null && s.audited_at !== null && s.block_time !== null, `loaded in ${rounds} more runs; cursor ${s.block}, as of ${s.block_time.toISOString()}`)

console.log('against the season read earlier with the in-memory store')
const memo = JSON.parse(readFileSync('/tmp/claude-0/-home-user/3a8d6223-ebc9-5af4-ae9c-0452ac68f60b/scratchpad/tx/season-rows.json', 'utf8'))
const oldCursor = memo.state.block
const dbRows = (await db.query(`select hash, block::int, tx_index, t::float8 as t, sent, to_addr as to, usdt::text from public.pool_tx where wallet = $1 and block <= $2 order by block, tx_index`, [W, oldCursor])).rows
const key = (r: any) => `${r.hash}|${r.block}|${r.tx_index}|${r.t}|${r.sent}|${r.to}|${r.usdt}`
const a = new Set(dbRows.map(key)), b = new Set((memo.rows as Row[]).map(key))
ok(a.size === b.size && [...a].every((k) => b.has(k)), `the ${b.size} rows up to block ${oldCursor} are identical, field for field`, `db ${a.size} vs ${b.size}`)
// peer: set exactly on the direct USDT transfers the wallet sent, nowhere else, and equal to the recipient in the receipt
const peerRows = (await db.query(`select hash, sent, to_addr, usdt::text, peer from public.pool_tx where wallet = $1`, [W])).rows
const direct = peerRows.filter((r: any) => r.sent && r.to_addr === USDT)
ok(direct.length >= 2 && direct.every((r: any) => /^0x[0-9a-f]{40}$/.test(r.peer ?? '')), `every direct USDT transfer the wallet sent (${direct.length}) has a recipient`)
ok(peerRows.filter((r: any) => !(r.sent && r.to_addr === USDT)).every((r: any) => r.peer === null), 'no other row has one (trades, top-ups and deposits stay null)')
const [receipts] = [await real(direct.map((r: any) => ({ method: 'eth_getTransactionReceipt', params: [r.hash] })))]
const rcpt = direct.map((r: any, i: number) => {
  const logs = (receipts[i].logs as any[]).filter((l) => l.address.toLowerCase() === USDT && l.topics[1].toLowerCase().endsWith(W.slice(2)))
  return logs.length === 1 && '0x' + String(logs[0].topics[2]).slice(26).toLowerCase() === r.peer
})
ok(rcpt.every(Boolean), 'each recipient equals the one in the transaction receipt, read separately from the node')
console.log('   direct transfers:', direct.map((r: any) => `${r.usdt} to ${r.peer}`).join(' | '))
const got = await trades()
const days: Record<string, [number, number]> = {}
for (const t of got) { const d = new Date(t.t + 3600_000).toISOString().slice(0, 10); (days[d] ??= [0, 0]); days[d][0]++; days[d][1] += t.profit }
const want: Record<string, [number, string]> = { '2026-10-29': [80, '6.69'], '2026-10-30': [384, '39.58'], '2026-11-02': [249, '28.85'], '2026-11-05': [260, '25.83'], '2026-11-10': [343, '83.88'], '2026-11-11': [415, '54.07'] }
for (const [d, [n, p]] of Object.entries(want)) ok(days[d]?.[0] === n && days[d][1].toFixed(2) === p, `${d}: ${n} trades, +$${p}`, JSON.stringify(days[d]))
console.log(`   season now: ${got.length} trades, +$${got.reduce((x, t) => x + t.profit, 0).toFixed(2)}`)

console.log('running again changes nothing')
const n0 = await count(), tr0 = JSON.stringify(await trades()), c0 = calls
const again = await syncChain(rpc, store, W, SINCE, { budgetMs: 25_000, audit: false })
const tr1 = await trades()
ok(again.reached && again.windows === 1 && calls - c0 <= 3, `one small range re-read (${calls - c0} requests)`)
ok((await count()) >= n0 && JSON.stringify(tr1.slice(0, JSON.parse(tr0).length)) === tr0, 'stored trades unchanged (new ones may only be added at the end)')

console.log('self-repair')
s = await st(); const cur = Number(s.block)
// (1) a made-up transaction in the recent zone disappears on the next run
const fake = '0x' + 'f'.repeat(64)
await db.query(`insert into public.pool_tx (wallet, hash, block, tx_index, t, sent, to_addr, usdt) values ($1,$2,$3,0,$4,true,'0xrouter',-999)`, [W, fake, cur - 100, Date.now()])
await syncChain(rpc, store, W, SINCE, { budgetMs: 25_000, audit: false })
ok((await db.query(`select 1 from public.pool_tx where wallet=$1 and hash=$2`, [W, fake])).rowCount === 0, 'a made-up recent transaction is removed by the next run')
// (2) a real transaction deleted from the last 24 hours comes back with the daily re-read
const victim = (await db.query(`select * from public.pool_tx where wallet=$1 and block < $2 and block > $3 order by block desc limit 1`, [W, cur - OVERLAP - 6000, cur - 80000])).rows[0]
if (!victim) ok(false, 'found a real transaction 2-22 hours old to delete')
else {
  await db.query(`delete from public.pool_tx where wallet=$1 and hash=$2`, [W, victim.hash])
  await syncChain(rpc, store, W, SINCE, { budgetMs: 25_000, audit: false })
  ok((await db.query(`select 1 from public.pool_tx where wallet=$1 and hash=$2`, [W, victim.hash])).rowCount === 0, 'a normal run does not look that far back (as designed)')
  await db.query(`update public.pool_sync set audited_at = now() - interval '25 hours' where wallet=$1`, [W])
  let r; let k = 0; do { r = await syncChain(rpc, store, W, SINCE, { budgetMs: 25_000, audit: true }); k++ } while (!r.reached && k < 10)
  const back = (await db.query(`select block, tx_index, sent, to_addr, usdt::text from public.pool_tx where wallet=$1 and hash=$2`, [W, victim.hash])).rows[0]
  ok(!!back && back.block === victim.block && back.usdt === victim.usdt && back.sent === victim.sent && back.to_addr === victim.to_addr, `the daily re-read restores it exactly (${k} run${k > 1 ? 's' : ''})`, JSON.stringify(back))
  ok(Date.now() - (await st()).audited_at.getTime() < 60_000, 'and records that the re-read was done')
}
// (3) a deleted transaction in the recent zone comes back on the next normal run (only testable if one exists)
const recent = (await db.query(`select * from public.pool_tx where wallet=$1 and block > $2 order by block desc limit 1`, [W, Number((await st()).block) - OVERLAP + 60])).rows[0]
if (!recent) console.log('  note   no real transaction in the last 30 minutes, so the recent-zone restore could not be exercised on live data')
else {
  await db.query(`delete from public.pool_tx where wallet=$1 and hash=$2`, [W, recent.hash])
  await syncChain(rpc, store, W, SINCE, { budgetMs: 25_000, audit: false })
  ok((await db.query(`select 1 from public.pool_tx where wallet=$1 and hash=$2 and usdt=$3`, [W, recent.hash, recent.usdt])).rowCount === 1, 'a deleted recent transaction is restored by the next run')
}
const final = await trades()
ok(JSON.stringify(final.slice(0, JSON.parse(tr0).length)) === tr0, 'after all the tampering and repair, the trades are exactly what they were')
console.log(`\n${pass} passed, ${fail} failed; ${calls} node requests in total`)
await db.end()
process.exit(fail ? 1 : 0)
