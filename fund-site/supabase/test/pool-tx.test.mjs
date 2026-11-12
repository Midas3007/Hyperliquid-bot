// 013_pool_tx.sql plus the peer column of 014_treasury.sql, on a throwaway local Postgres: every property that was proven for
// 013 (pool_apply, pool_trades against hand-made cases, every season trade identical to the reference loop) still holds, and
// the new peer column is carried, never erased, and never changes what pool_trades returns.
// Adapted from the original 013 test (its own database `treasury_pooltx`, 014 applied on top).
import { readFileSync } from 'node:fs'
import { buildDb, SQL } from './lib-db.mjs'
const ROWS = '/tmp/claude-0/-home-user/3a8d6223-ebc9-5af4-ae9c-0452ac68f60b/scratchpad/tx/season-rows.json'
const USDT = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
let pass = 0, fail = 0
const ok = (cond, name, extra = '') => { if (cond) { pass++; console.log('  ok   ', name) } else { fail++; console.log('  FAIL ', name, extra) } }
const throws = async (fn, re, name) => { try { await fn(); ok(false, name, 'did not throw') } catch (e) { ok(re.test(e.message), name, e.message) } }
const db = await buildDb('treasury_pooltx', ['009_alerts.sql', '013_pool_tx.sql'])
const sql = readFileSync(`${SQL}/013_pool_tx.sql`, 'utf8')
console.log('migration')
await db.query(sql); ok(true, '013 runs again on top of itself')
const m14 = readFileSync(`${SQL}/014_treasury.sql`, 'utf8')
await db.query(m14); await db.query(m14); ok(true, '014 runs twice on top of 013')
const jobs = (await db.query(`select jobname, schedule, command from cron.job where jobname in ('pool-trades-sync', 'treasury-sync') order by 1`)).rows
ok(jobs.length === 2 && jobs[0].jobname === 'pool-trades-sync' && jobs[0].schedule === '*/2 * * * *', 'the 013 sync job is still scheduled once, unchanged', JSON.stringify(jobs.map((j) => [j.jobname, j.schedule])))
ok(/functions\/v1\/trade-stats/.test(jobs[0].command) && /"cron": true/.test(jobs[0].command), 'sync job calls trade-stats with {cron:true}')

console.log('permissions')
const priv = async (role, what) => (await db.query(`select ${what} as p`, [role])).rows[0].p
ok(!(await priv('anon', `has_function_privilege($1, 'public.pool_trades(text,bigint)', 'execute')`)), 'anon cannot run pool_trades')
ok(!(await priv('authenticated', `has_function_privilege($1, 'public.pool_apply(text,bigint,bigint,timestamptz,jsonb,boolean)', 'execute')`)), 'signed-in users cannot run pool_apply (still true after 014 re-creates it)')
ok(await priv('service_role', `has_function_privilege($1, 'public.pool_apply(text,bigint,bigint,timestamptz,jsonb,boolean)', 'execute')`), 'service role can run pool_apply')
ok(await priv('service_role', `has_function_privilege($1, 'public.pool_trades(text,bigint)', 'execute')`), 'service role can run pool_trades')
ok(!(await priv('anon', `has_table_privilege($1, 'public.pool_tx', 'select')`)) && !(await priv('authenticated', `has_table_privilege($1, 'public.pool_sync', 'select')`)), 'anon / signed-in users cannot read the tables')
ok((await db.query(`select bool_and(relrowsecurity) b from pg_class where relname in ('pool_tx','pool_sync')`)).rows[0].b, 'row level security is still on for both tables')

// --- helpers
const apply = (w, lo, hi, rows, replace = false, t = '2026-11-01T00:00:00Z') => db.query(`select public.pool_apply($1,$2,$3,$4,$5::jsonb,$6) n`, [w, lo, hi, t, JSON.stringify(rows), replace]).then((r) => r.rows[0].n)
const trades = (w, since = 0) => db.query(`select public.pool_trades($1,$2) j`, [w, since]).then((r) => r.rows[0].j)
const H = (n) => '0x' + n.toString(16).padStart(64, '0')
const row = (n, block, usdt, o = {}) => ({ hash: H(n), block, tx_index: o.i ?? 0, t: o.t ?? block * 1000, sent: o.sent ?? true, to: o.to ?? '0xrouter', usdt })
const state = (w) => db.query(`select * from public.pool_sync where wallet = $1`, [w]).then((r) => r.rows[0])

console.log('pool_apply')
await throws(() => apply('w1', 1, 10, []), /no reading started/, 'refuses a wallet with no sync state')
await db.query(`insert into public.pool_sync (wallet, from_block, block, since_ms) values ('w1', 100, 99, 0)`)
await throws(() => apply('w1', 100, 200, [row(1, 201, '-5.000000')]), /outside blocks/, 'refuses a row outside the range')
await throws(() => apply('w1', 200, 100, []), /bad range/, 'refuses an upside-down range')
await throws(() => db.query(`select public.pool_apply('w1', 100, 200, now(), '{}'::jsonb, false)`), /bad range or rows/, 'refuses rows that are not a list')
await throws(() => apply('w1', 100, 200, [{ ...row(1, 150, '-5.000000'), hash: '0xABC' }]), /check|violates/, 'refuses a malformed hash')
ok((await state('w1')).block === '99', 'a refused call leaves the cursor alone')
ok((await apply('w1', 100, 200, [])) === 0 && (await state('w1')).block === '200', 'an empty range moves the cursor')
await apply('w1', 201, 300, [row(1, 210, '-100.000000'), row(2, 220, '100.250000')], false, '2026-11-01T00:05:00Z')
let s = await state('w1'); ok(s.block === '300' && s.block_time.toISOString() === '2026-11-01T00:05:00.000Z', 'cursor and its time follow the range')
await apply('w1', 150, 250, [row(1, 210, '-100.000000'), row(2, 220, '100.250000')], false, '2026-10-31T00:00:00Z')
s = await state('w1'); ok(s.block === '300' && s.block_time.toISOString() === '2026-11-01T00:05:00.000Z', 'an older range never moves the cursor back')
ok((await db.query(`select count(*)::int n from public.pool_tx where wallet='w1'`)).rows[0].n === 2, 'storing the same rows twice keeps one copy')
// replace: only inside the range, only what is missing
await apply('w1', 301, 400, [row(3, 310, '-50.000000'), row(4, 320, '50.100000'), row(5, 390, '-7.000000')])
await apply('w1', 301, 350, [row(3, 310, '-50.000000')], true)
let left = (await db.query(`select hash from public.pool_tx where wallet='w1' order by block`)).rows.map((r) => parseInt(r.hash, 16))
ok(JSON.stringify(left) === '[1,2,3,5]', 'replace removes what vanished inside the range and nothing outside it', JSON.stringify(left))
await apply('w1', 301, 350, [row(3, 310, '-51.000000')], false)
ok((await db.query(`select usdt from public.pool_tx where wallet='w1' and hash=$1`, [H(3)])).rows[0].usdt === '-51.000000', 'a re-read updates the stored amount')
await db.query(`insert into public.pool_sync (wallet, from_block, block, since_ms) values ('w2', 1, 0, 0)`)
await apply('w2', 1, 400, [row(1, 210, '-1.000000')])
ok((await db.query(`select count(*)::int n from public.pool_tx where wallet='w1'`)).rows[0].n === 4, 'another wallet with the same hash does not touch this one')

console.log('pool_trades: hand-made cases')
const fresh = async (w, rows) => { await db.query(`delete from public.pool_tx where wallet=$1; delete from public.pool_sync where wallet=$1; insert into public.pool_sync (wallet, from_block, block, since_ms) values ($1, 1, 0, 0)`.replaceAll('$1', `'${w}'`)); await apply(w, 1, 10_000, rows) }
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
await fresh('c', []); ok(same(await trades('c'), []), 'no rows -> an empty list')
await fresh('c', [row(1, 10, '-100.000000'), row(2, 20, '100.300000')])
ok(same(await trades('c'), [{ t: 20000, size: 100, profit: 0.3 }]), 'buy then sale', JSON.stringify(await trades('c')))
await fresh('c', [row(1, 10, '-100.000000'), row(2, 20, '-50.500000'), row(3, 30, '150.000000')])
ok(same(await trades('c'), [{ t: 30000, size: 150.5, profit: -0.5 }]), 'two buys then one sale: one trade, losses included', JSON.stringify(await trades('c')))
await fresh('c', [row(1, 10, '77.000000'), row(2, 20, '-100.000000'), row(3, 30, '100.100000')])
ok(same(await trades('c'), [{ t: 30000, size: 100, profit: 0.1 }]), 'a sale with nothing bought before it is ignored', JSON.stringify(await trades('c')))
await fresh('c', [row(1, 10, '-100.000000'), row(2, 20, '100.100000'), row(3, 30, '5.000000'), row(4, 40, '-10.000000')])
ok(same(await trades('c'), [{ t: 20000, size: 100, profit: 0.1 }]), 'a second sale in a row is ignored; a trailing buy stays open', JSON.stringify(await trades('c')))
await fresh('c', [row(1, 10, '-100.000000'), row(2, 15, '-500.000000', { to: USDT }), row(3, 16, '900.000000', { sent: false }), row(4, 17, '0.000000'), row(5, 20, '100.100000')])
ok(same(await trades('c'), [{ t: 20000, size: 100, profit: 0.1 }]), 'withdrawals (to USDT), deposits (not sent) and zero-change rows are left out', JSON.stringify(await trades('c')))
await fresh('c', [row(1, 10, '-100.000000'), row(2, 20, '100.100000'), row(3, 30, '-200.000000'), row(4, 40, '200.500000')])
ok(same(await trades('c', 25000), [{ t: 40000, size: 200, profit: 0.5 }]), 'only legs at or after the season start count')
ok(same(await trades('c', 20000), [{ t: 40000, size: 200, profit: 0.5 }]), 'a sale exactly at the start with its buy before it: ignored, like before')
await fresh('c', [row(2, 20, '100.200000', { i: 5 }), row(1, 20, '-100.000000', { i: 2 }), row(3, 20, '-30.000000', { i: 9 }), row(4, 21, '30.010000', { i: 0 })])
ok(same(await trades('c'), [{ t: 20000, size: 100, profit: 0.2 }, { t: 21000, size: 30, profit: 0.01 }]), 'legs in one block are ordered by their position in it', JSON.stringify(await trades('c')))
ok(same(await trades('nobody'), []), 'an unknown wallet -> an empty list')

console.log('pool_trades: the real season')
const { state: st, rows } = JSON.parse(readFileSync(ROWS, 'utf8'))
const W = '0x6cd37bc290c5c02ea92086d416a6a673bca581dc', SINCE = st.since_ms
await db.query(`insert into public.pool_sync (wallet, from_block, block, since_ms) values ($1, $2, $3, $4)`, [W, st.from_block, st.from_block - 1, SINCE])
let n = 0
for (let a = st.from_block; a <= st.block; a += 5000) { const b = Math.min(a + 4999, st.block); const part = rows.filter((r) => r.block >= a && r.block <= b); n += await apply(W, a, b, part, false, new Date(st.block_time).toISOString()) }
ok(n === rows.length && (await db.query(`select count(*)::int n from public.pool_tx where wallet=$1`, [W])).rows[0].n === rows.length, `all ${rows.length} real rows stored`)
ok((await state(W)).block === String(st.block), 'cursor at the last block read')
// the JS reference: the loop the function has always used, on exact integers
const micro = (x) => { const neg = x.startsWith('-'); const [i, f = ''] = x.replace('-', '').split('.'); const v = BigInt(i) * 1000000n + BigInt((f + '000000').slice(0, 6)); return neg ? -v : v }
const ref = []; let open = 0n
for (const r of [...rows].sort((x, y) => x.block - y.block || x.tx_index - y.tx_index)) { if (!r.sent || r.t < SINCE) continue; const d = micro(r.usdt); if (d === 0n || r.to === USDT) continue; if (d < 0n) open += -d; else if (open > 0n) { ref.push({ t: r.t, size: Number(open) / 1e6, profit: Number(d - open) / 1e6 }); open = 0n } }
const got = await trades(W, SINCE)
ok(got.length === ref.length, `same number of trades as the original loop (${ref.length})`, `sql ${got.length}`)
let diff = 0; for (let i = 0; i < ref.length; i++) if (!got[i] || got[i].t !== ref[i].t || got[i].size !== ref[i].size || got[i].profit !== ref[i].profit) { if (diff++ < 3) console.log('   differs at', i, JSON.stringify(got[i]), JSON.stringify(ref[i])) }
ok(diff === 0, 'every trade identical: time, size and profit')
const sum = (a, k) => a.reduce((x, y) => x + y[k], 0)
console.log(`   season: ${got.length} trades, profit ${sum(got, 'profit').toFixed(2)}, traded ${sum(got, 'size').toFixed(0)}`)
// per-day check in SQL too (Lagos days), against days already confirmed from Telegram
const days = (await db.query(`select to_char(to_timestamp((x->>'t')::bigint/1000) at time zone 'Africa/Lagos','YYYY-MM-DD') d, count(*)::int n, round(sum((x->>'profit')::numeric),2)::text p from jsonb_array_elements(public.pool_trades($1,$2)) x group by 1 order by 1`, [W, SINCE])).rows
const want = { '2026-10-29': [80, '6.69'], '2026-10-30': [384, '39.58'], '2026-10-31': [116, '7.19'], '2026-11-01': [194, '11.92'], '2026-11-02': [249, '28.85'], '2026-11-03': [224, '10.10'], '2026-11-04': [136, '10.22'], '2026-11-05': [260, '25.83'] }
for (const [d, [wn, wp]] of Object.entries(want)) { const g = days.find((x) => x.d === d); ok(g && g.n === wn && g.p === wp, `${d}: ${wn} trades, +$${wp}`, JSON.stringify(g)) }
const t0 = Date.now(); for (let i = 0; i < 20; i++) await trades(W, SINCE); console.log(`   pool_trades takes ${((Date.now() - t0) / 20).toFixed(1)} ms on ${rows.length} rows`)



// ---------- the peer column (014) ----------
console.log('peer: carried by pool_apply, never erased, no effect on the trades')
const P = '0x' + '5'.repeat(40), D1 = '0x' + 'a'.repeat(40), D2 = '0x' + 'b'.repeat(40)
await fresh('p', [])
const prow = (n, peer, o = {}) => ({ ...row(n, 10 + n, '-8.000000', { to: USDT, ...o }), ...(peer === undefined ? {} : { peer }) })
await apply('p', 1, 10_000, [prow(1, D1), prow(2, null), prow(3)])
const peers = async () => (await db.query(`select hash, peer from public.pool_tx where wallet = 'p' order by block`)).rows.map((r) => r.peer)
ok(same(await peers(), [D1, null, null]), 'a row carries its peer; a null peer and a missing peer key both store null')
await apply('p', 1, 10_000, [prow(1), prow(2, D2), prow(3, null)])
ok(same(await peers(), [D1, D2, null]), 'a re-read with no peer keeps the stored peer; a re-read that has one fills it in')
await apply('p', 1, 10_000, [prow(1, D2)])
ok(same(await peers(), [D2, D2, null]), 'a re-read with a different peer replaces it (the chain is the authority)')
await throws(() => apply('p', 1, 10_000, [prow(4, '0xABC')]), /pool_tx_peer_shape/, 'a malformed peer is refused')
await apply('p', 1, 10_000, [prow(5, '')]); ok((await peers()).includes(null), 'an empty peer is stored as null')
await fresh('q1', [row(1, 10, '-100.000000'), row(2, 20, '100.300000'), row(3, 25, '-5.000000', { to: USDT }), row(4, 30, '-50.000000'), row(5, 40, '50.100000')])
const before = JSON.stringify(await trades('q1'))
await apply('q1', 1, 10_000, [{ ...row(3, 25, '-5.000000', { to: USDT }), peer: D1 }, { ...row(1, 10, '-100.000000'), peer: D2 }])
ok(JSON.stringify(await trades('q1')) === before, 'adding peers to rows (even to a trade leg) leaves pool_trades exactly as it was')
// the season, stored again with peers filled in for direct transfers: identical trades
const withPeers = rows.map((r) => (r.sent && r.to === USDT ? { ...r, peer: '0x' + '9'.repeat(40) } : r))
await db.query(`delete from public.pool_tx where wallet = $1`, [W])
for (let a = st.from_block; a <= st.block; a += 5000) { const b = Math.min(a + 4999, st.block); await apply(W, a, b, withPeers.filter((r) => r.block >= a && r.block <= b), false, new Date(st.block_time).toISOString()) }
const got2 = await trades(W, SINCE)
ok(same(got2, got), `the season stored with peers gives the same ${got.length} trades, field for field`)
ok((await db.query(`select count(*)::int n from public.pool_tx where wallet = $1 and peer is not null`, [W])).rows[0].n === withPeers.filter((r) => r.peer).length, 'only the direct transfers carry a peer')

console.log(`\n${pass} passed, ${fail} failed`)
await db.end()
process.exit(fail ? 1 : 0)
