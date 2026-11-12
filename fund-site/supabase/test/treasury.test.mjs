// Tests for 014_treasury.sql on a throwaway local Postgres (never the live project).
//   node treasury.test.mjs
// It creates its own database `treasury_test` (dropped and rebuilt every run), loads the real 009 and 013 migrations,
// copies the real season rows of the bot wallet out of the local `postgres` database (read only), runs 014 twice,
// and checks the accounting on the real rows and on hand-made cases. Also checks the keeper's pure decision code.
// Start Postgres first and set PG_MODULE if `pg` is not installed here.
import { writeFileSync } from 'node:fs'
import { BOT, SQL, VAULT, buildDb, conn } from './lib-db.mjs'

const USDT = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
const ROUTER = '0x5615cdab10dc425a742d643d949a7f474c01abc4'
const DAY = 86_400_000
const DEFAULT_START = Date.parse('2026-11-11T23:00:00Z') // the start of 12 Nov 2026, Lagos time

let pass = 0, fail = 0
const ok = (cond, name, extra = '') => { if (cond) { pass++; console.log('  ok   ', name) } else { fail++; console.log('  FAIL ', name, extra) } }
const near = (a, b, eps = 1e-5) => Math.abs(Number(a) - Number(b)) <= eps
const throws = async (fn, re, name) => { try { await fn(); ok(false, name, 'did not throw') } catch (e) { ok(re.test(e.message), name, e.message) } }

// ---------- build the database ----------
const src = conn('postgres'); await src.connect() // read only: the real season rows
const realTx = (await src.query(`select * from public.pool_tx where wallet = $1 order by block, tx_index`, [BOT])).rows
const realSync = (await src.query(`select * from public.pool_sync where wallet = $1`, [BOT])).rows[0]
await src.end()
console.log('migration')
const db = await buildDb('treasury_test', ['009_alerts.sql', '013_pool_tx.sql'])
await db.query(`insert into public.pool_sync (wallet, from_block, block, block_time, since_ms, synced_at) values ($1,$2,$3,$4,$5,$6)`,
  [BOT, realSync.from_block, realSync.block, realSync.block_time, realSync.since_ms, realSync.synced_at])
await db.query(`insert into public.pool_tx (wallet, hash, block, tx_index, t, sent, to_addr, usdt)
  select $1, x->>'hash', (x->>'block')::bigint, (x->>'tx_index')::int, (x->>'t')::bigint, (x->>'sent')::boolean, x->>'to_addr', (x->>'usdt')::numeric from jsonb_array_elements($2::jsonb) x`,
  [BOT, JSON.stringify(realTx)]) // stored the way 013 stored them: no peer yet
const SINCE = Number(realSync.since_ms)
const CURSOR = new Date(realSync.block_time).getTime()
const { readFileSync } = await import('node:fs')
const mig = readFileSync(`${SQL}/014_treasury.sql`, 'utf8')
await db.query(mig); ok(true, 'runs on a database with the real 013 tables and the stored rows')
await db.query(mig); ok(true, 'runs a second time (safe to repeat)')
const jobs = (await db.query(`select jobname, schedule, command from cron.job where jobname = 'treasury-sync'`)).rows
ok(jobs.length === 1 && jobs[0].schedule === '*/5 * * * *' && /treasury_sync/.test(jobs[0].command), 'the 5-minute schedule exists once')
ok((await db.query(`select count(*)::int n from public.treasury_config`)).rows[0].n === 1, 'one settings row after two runs')
ok((await db.query(`select count(*)::int n from public.pool_tx where peer is not null`)).rows[0].n === 0 && (await db.query(`select count(*)::int n from public.pool_tx`)).rows[0].n === realTx.length, 'existing pool_tx rows are untouched: peer is null until they are read again')
const dflt = (await db.query(`select floor(extract(epoch from start_at) * 1000)::bigint s, active, settle_hour_utc from public.treasury_config`)).rows[0]
ok(Number(dflt.s) === DEFAULT_START && dflt.active === false && dflt.settle_hour_utc === 20, 'stored default: counts from 2026-11-11T23:00:00Z (start of 12 Nov, Lagos), off until started, settlement day ends 20:00 UTC')

console.log('permissions')
const priv = async (role, what) => (await db.query(`select ${what} as p`, [role])).rows[0].p
const fn = (sig) => `has_function_privilege($1, '${sig}', 'execute')`
for (const f of ['public.treasury_sync(timestamptz)', 'public.treasury_carve(timestamptz)', 'public.treasury_status(bigint,timestamptz)', 'public._treasury_balance()', 'public._treasury_profit(text,bigint,bigint)', 'public._treasury_advance(timestamptz)', 'public._treasury_book_gas(bigint)', 'public.pool_apply(text,bigint,bigint,timestamptz,jsonb,boolean)']) {
  ok(!(await priv('anon', fn(f))) && !(await priv('authenticated', fn(f))) && (await priv('service_role', fn(f))), `${f}: service role only`)
}
ok((await priv('anon', fn('public.treasury_overview()'))) && (await priv('authenticated', fn('public.treasury_overview()'))), 'members and visitors can read the overview')
for (const f of ['public.admin_treasury_start()', 'public.admin_treasury_record_render(numeric,date,text,text,text,boolean)', 'public.admin_treasury_sweep(numeric)', 'public.admin_treasury_cancel_render(bigint)', 'public.admin_treasury_settings(numeric,boolean,numeric,integer,timestamptz)', 'public.admin_treasury_admin()']) {
  ok(!(await priv('anon', fn(f))) && (await priv('authenticated', fn(f))), `${f}: not for anon, checks is_admin inside`)
}
ok(!(await priv('anon', `has_table_privilege($1, 'public.treasury_entries', 'select')`)) && !(await priv('authenticated', `has_table_privilege($1, 'public.treasury_config', 'select')`)) && !(await priv('authenticated', `has_table_privilege($1, 'public.treasury_days', 'select')`)), 'visitors and members cannot read the tables')
ok((await db.query(`select bool_and(relrowsecurity) b from pg_class where relname in ('treasury_config','treasury_days','treasury_entries')`)).rows[0].b, 'row level security is on for all three tables')
ok((await db.query(`select bool_and(prosecdef and array_to_string(proconfig, ',') like '%search_path=""%') b from pg_proc where proname ~ 'treasury'`)).rows[0].b, 'every treasury function is security definer with an empty search_path')

// ---------- helpers ----------
const asRole = async (role, uid, sql, args = []) => {
  await db.query('begin'); try {
    await db.query(`set local role ${role}`); if (uid) await db.query(`select set_config('request.jwt.claim.sub', $1, true)`, [uid])
    const r = await db.query(sql, args); await db.query('commit'); return r
  } catch (e) { await db.query('rollback'); throw e }
}
const ADMIN = '00000000-0000-0000-0000-0000000000aa', MEMBER = '00000000-0000-0000-0000-0000000000bb'
const q = async (sql, args = []) => (await db.query(sql, args)).rows
const one = async (sql, args = []) => (await db.query(sql, args)).rows[0]
const H = (n) => '0x' + n.toString(16).padStart(64, '0')
const A = (c) => '0x' + c.repeat(40)
const DEST = A('d'), DEST2 = A('e'), OTHER_ROUTER = '0xa9aa0a64769cbed4d3b1ceb4df01cde915c235b3'
const reset = async (wallet, o = {}) => {
  await db.query(`truncate public.treasury_entries, public.treasury_days restart identity`)
  await db.query(`delete from public.pool_tx where wallet = $1`, [wallet === BOT ? '0x' + '0'.repeat(40) : wallet]) // hand-made wallets start empty; the real rows stay
  await db.query(`update public.treasury_config set active = true, wallet = $1, start_at = to_timestamp($2 / 1000.0), carve_pct = 0.10, carry_losses = $3, hold_minutes = 180 where id = 1`, [wallet, o.start ?? 0, o.carry ?? true])
}
const W2 = '0x' + '2'.repeat(40)
const setSync = async (wallet, ms, since = 0) => {
  await db.query(`insert into public.pool_sync (wallet, from_block, block, block_time, since_ms, synced_at) values ($1, 1, $2, to_timestamp($3 / 1000.0), $4, now())
    on conflict (wallet) do update set block = excluded.block, block_time = excluded.block_time, synced_at = excluded.synced_at, since_ms = excluded.since_ms`, [wallet, Math.floor(ms / 1000), ms, since])
}
let seq = 100
const tx = async (wallet, o) => { // one row of pool_tx (create them in time order: the block order is the creation order)
  const n = ++seq
  await db.query(`insert into public.pool_tx (wallet, hash, block, tx_index, t, sent, to_addr, usdt, peer) values ($1,$2,$3,0,$4,$5,$6,$7,$8)
    on conflict (wallet, hash) do update set usdt = excluded.usdt`, [wallet, o.hash ?? H(n), o.block ?? n, o.t, o.sent ?? true, o.to ?? OTHER_ROUTER, o.usdt, o.peer ?? null])
  return o.hash ?? H(n)
}
const trade = async (w, t, size, profit) => { await tx(w, { t, usdt: -size }); await tx(w, { t: t + 1000, usdt: size + profit }) } // buy then sale
const topup = (w, t, amt = 0.5, hash) => tx(w, { t, usdt: -amt, to: ROUTER, hash })
const withdraw = (w, t, amt, peer, o = {}) => tx(w, { t, usdt: -amt, to: USDT, peer, ...o })
const pool = async () => Number((await one(`select public._treasury_balance() b`)).b)
const iso = (ms) => `to_timestamp(${ms} / 1000.0)`
const carve = async (ms) => (await one(`select public.treasury_carve(${iso(ms)}) r`)).r
const sync = async (ms) => (await one(`select public.treasury_sync(${iso(ms ?? Date.now())}) r`)).r
const entries = () => q(`select * from public.treasury_entries order by id`)
const snapshot = async () => ({
  entries: (await q(`select kind, status, amount::text, pot_delta::text, uncovered::text, t_ms::text, day::text, tx from public.treasury_entries order by id`)),
  days: (await q(`select day::text, win_from::text, win_to::text, trades, profit::text, carry_in::text, carry_out::text, carve::text from public.treasury_days order by day`)),
})
const T0 = Math.floor((Date.now() - 30 * DAY) / DAY) * DAY // hand-made cases start 30 days ago (midnight UTC)
const RUN = (k) => T0 + k * DAY + 20.2 * 3600_000 // the keeper's run on hand-made day k (just after the 20:00 end of that day)
const AT = (k, h) => T0 + k * DAY + h * 3600_000

// =====================================================================================================
console.log('top-ups on the real season rows')
const tops = await q(`select hash, usdt from public.pool_tx where wallet = $1 and sent and to_addr = $2 and usdt < 0 and usdt >= -1`, [BOT, ROUTER])
ok(tops.length === 28, 'the rule finds exactly the 28 real top-ups', String(tops.length))
ok(near(tops.reduce((s, r) => s - Number(r.usdt), 0), 13.730151, 1e-9), 'they add up to $13.730151 (the $13.73 measured on the chain)')
const others = await q(`select min(-usdt) m from public.pool_tx where wallet = $1 and sent and to_addr = $2 and usdt < 0 and usdt < -1`, [BOT, ROUTER])
ok(Number(others[0].m) > 19, 'the smallest router buy that is NOT a top-up is $' + Number(others[0].m).toFixed(2) + ' (a wide gap from $0.50)')
ok(Math.max(...tops.map((r) => -Number(r.usdt))) <= 0.5, 'no top-up is above the bot\'s $0.50 cap')

console.log('profit definition on the real rows (no double charging)')
const net = Number((await one(`select sum((x->>'profit')::numeric) s, count(*)::int n from jsonb_array_elements(public.pool_trades($1, $2)) x`, [BOT, SINCE])).s)
ok(near(net, 334.698424, 1e-6), 'the dashboard counter (pool_trades) shows +$334.70, already net of top-ups', String(net))
await db.query(`update public.treasury_config set wallet = $1, start_at = to_timestamp($2 / 1000.0) where id = 1`, [BOT, SINCE])
const gross = await one(`select public._treasury_profit($1, 0, $2) j`, [BOT, CURSOR + DAY])
ok(near(gross.j.profit, 348.428575, 1e-6) && gross.j.trades === 3144, 'treasury profit (top-ups left out) is $348.43 over the same 3,144 trades', JSON.stringify(gross.j))
ok(near(Number(gross.j.profit) - net, 13.730151, 1e-6), 'the difference is exactly the top-ups')

// =====================================================================================================
console.log('the whole season: live day by day versus one catch-up run (hypothetical start at the season start)')
await reset(BOT, { start: SINCE })
const days = []
for (let d = Date.parse('2026-10-29T20:07:00Z'); d < CURSOR; d += DAY) days.push(d)
let minPot = Infinity, maxCarve = 0
for (const d of days) {
  await setSync(BOT, Math.min(d, CURSOR), SINCE)
  const r = await sync(d) // the run at 20:07 on that day
  minPot = Math.min(minPot, await pool())
}
await setSync(BOT, CURSOR, SINCE); await sync(CURSOR + 60_000)
maxCarve = Number((await one(`select max(amount) m from public.treasury_entries where kind = 'carve'`)).m)
const live = await snapshot()
const potLive = await pool()
const cu = await carve(Date.now()) // no new day: the next one ends at 20:00 UTC today and the history stops at 11:09
ok(cu.status === 'already' || cu.status === 'stale', 'live run: nothing more is due once the last finished day is carved (' + cu.status + ')')
// the catch-up: the treasury is switched on late; one run does everything
await reset(BOT, { start: SINCE })
await setSync(BOT, CURSOR, SINCE)
const t1 = await sync(CURSOR + 60_000)
const caught = await snapshot()
ok(JSON.stringify(live) === JSON.stringify(caught), `one catch-up run books ${caught.entries.length} entries and ${caught.days.length} days, identical field for field to the day-by-day run`)
ok(t1.days_carved === days.length, `the catch-up carved ${t1.days_carved} days in that one run`)
const again = await sync(CURSOR + 120_000)
ok(JSON.stringify(await snapshot()) === JSON.stringify(caught) && again.days_carved === 0 && again.gas_recorded === 0, 'a second run carves and books nothing again (idempotent)')
ok(near(await pool(), potLive, 1e-9), 'pot after catch-up equals the pot of the live run')
const sum = async (k, col = 'amount') => Number((await one(`select coalesce(sum(${col}),0) s from public.treasury_entries where kind = $1 and status = 'done'`, [k])).s)
const carved = await sum('carve'), gasAll = await sum('gas'), gasFromPot = -(await sum('gas', 'pot_delta')), unc = Number((await one(`select coalesce(sum(uncovered),0) s from public.treasury_entries`)).s)
const potNow = await pool()
const dayRows = await q(`select * from public.treasury_days order by day`)
const winProfit = dayRows.reduce((s, r) => s + Number(r.profit), 0)
ok(dayRows.length === days.length && dayRows[0].day.toISOString?.().slice(0, 10) === '2026-10-29' || String(dayRows[0].day).includes('2026-10-29'), `one carve row per settlement day (${dayRows.length} days)`)
ok(dayRows.every((r, i) => i === 0 ? true : Number(r.win_from) === Number(dayRows[i - 1].win_to)) && dayRows.every((r) => Number(r.win_to) % 3_600_000 === 0 && new Date(Number(r.win_to)).getUTCHours() === 20), 'windows are exactly 20:00 UTC to 20:00 UTC and each starts where the last ended (no gap, no overlap)')
const after = Number((await one(`select public._treasury_profit($1, $2, $3) j`, [BOT, Number(dayRows.at(-1).win_to), CURSOR + DAY])).j.profit)
ok(near(winProfit + after, 348.428575, 1e-5), `window profits ($${winProfit.toFixed(2)}) plus the not-yet-ended day ($${after.toFixed(2)}) add up to $348.43`)
ok(near(carved, 0.1 * winProfit, 2e-5), `carved in total = 10% of the finished days' profit = $${carved.toFixed(4)}`)
ok(near(gasAll, 13.730151, 1e-9), 'all 28 top-ups were logged once: $13.73')
ok(near(gasFromPot + unc, 13.730151, 1e-9), `gas = paid by the pot ($${gasFromPot.toFixed(2)}) + paid by members ($${unc.toFixed(2)})`)
ok(near(potNow, carved - gasFromPot, 1e-9) && potNow >= 0 && minPot >= 0, `the pot never went below zero; it would hold $${potNow.toFixed(2)} today`)
const members = net - potNow // wallet USDT change from closed trades (gas included) minus the pot
ok(near(members, (348.428575 - carved) - unc, 5e-5), `members' value = wallet - pot = gross profit minus carved minus the gas the pot could not cover ($${members.toFixed(2)})`)
ok(maxCarve / 590 < 0.03, `the biggest single carve is $${maxCarve.toFixed(2)}, ${(100 * maxCarve / 590).toFixed(2)}% of a $590 wallet: nowhere near the contract's 50% daily limit`)
console.log(`         first-day gas paid by members (pot still empty): $${unc.toFixed(2)}; gas per day (last 14 d): $${Number((await one(`select public._treasury_gas_per_day(${iso(CURSOR)}) g`)).g).toFixed(3)}`)
const ov = (await one(`select public.treasury_overview() o`)).o
ok(near(ov.balance, potNow, 1e-6) && ov.log.length > 40 && ov.log.length <= 100 && ov.log[0].t >= ov.log[1].t, 'overview: balance matches, log is newest first (at most 100 lines)')
ok(!JSON.stringify(ov).includes(BOT) && !('wallet' in ov) && !('unmatched' in ov), 'overview shows no pool wallet address and no admin data')
if (process.env.TREASURY_OVERVIEW_OUT) writeFileSync(process.env.TREASURY_OVERVIEW_OUT, JSON.stringify(ov, null, 1)) // for the screenshots
console.log(`         overview: balance $${Number(ov.balance).toFixed(2)}, ${ov.days_covered} days of costs covered (costs $${ov.cost_per_day}/day), full at $${ov.target_balance}`)

// =====================================================================================================
console.log('the start boundary (rule: a trade counts if its first buy is at or after the start; its sale decides the day; a top-up counts if at or after the start)')
const S = DEFAULT_START, W3 = '0x' + '3'.repeat(40)
const profitOf = async () => (await one(`select public._treasury_profit($1, 0, $2) j`, [W3, S + 5 * DAY])).j
const scenario = async (legs, topups = []) => { await reset(W3, { start: S }); await setSync(W3, S + 12 * 3600_000); for (const [t, u] of legs) await tx(W3, { t, usdt: u }); for (const t of topups) await topup(W3, t); }
await scenario([[S - 3_600_000, -100], [S + 1_800_000, 101]])
ok((await profitOf()).trades === 0, 'bought an hour before the start, sold after it: not counted (it belongs to the time before the start)')
await scenario([[S, -100], [S + 1_800_000, 101]]); const bAt = await profitOf()
ok(bAt.trades === 1 && near(bAt.profit, 1), 'bought exactly at the start: counted')
await scenario([[S - 1, -100], [S + 1_800_000, 101]])
ok((await profitOf()).trades === 0, 'bought one millisecond before the start: not counted')
await scenario([[S - 7_200_000, -100], [S - 3_600_000, 101]])
ok((await profitOf()).trades === 0, 'a whole trade before the start: not counted')
await scenario([], [S - 1, S])
await sync(S + 12 * 3600_000)
ok((await entries()).length === 1 && Number((await entries())[0].t_ms) === S, 'a top-up exactly at the start is booked; one a millisecond earlier is not')
// the edges of a settlement day
const W4 = '0x' + '4'.repeat(40), B1 = Date.parse('2026-11-12T20:00:00Z')
await reset(W4, { start: S }); await setSync(W4, B1 + 3600_000)
await tx(W4, { t: B1 - 2000, usdt: -100 }); await tx(W4, { t: B1, usdt: 110 }) // sale exactly at 20:00:00.000: this day
await tx(W4, { t: B1 + 1000, usdt: -100 }); await tx(W4, { t: B1 + 2000, usdt: 120 }) // sale after: the next day
await sync(B1 + 600_000)
const d1 = await q(`select day::text, profit::text, trades from public.treasury_days order by day`)
ok(d1.length === 1 && d1[0].day === '2026-11-12' && near(d1[0].profit, 10) && d1[0].trades === 1, 'a sale exactly at 20:00:00.000 is in that day; one a second later is not')
ok(Number((await q(`select win_from::text w from public.treasury_days`))[0].w) === Date.parse('2026-11-11T20:00:00Z'), 'the first day is 12 Nov (11 Nov ended at 20:00 UTC, before the start); its window opens at 11 Nov 20:00 UTC')
await setSync(W4, B1 + DAY + 3600_000); await sync(B1 + DAY + 600_000)
ok(near((await q(`select profit::text p from public.treasury_days where day = '2026-11-13'`))[0].p, 20), 'the trade sold one second after 20:00 is carved with the next day')

console.log('catch-up after a late switch-on, default start, with a losing day (hand-made)')
const feed = async (w, upto) => { // data for 12-16 Nov; one trade a day, created in time order
  const prof = [10, -6, 0, 10, 20]
  for (let k = 0; k < upto; k++) {
    const day = Date.parse('2026-11-12T00:00:00Z') + k * DAY
    if (k === 0) await tx(w, { t: S - 600_000, usdt: -50 }), await tx(w, { t: S - 500_000, usdt: 49 }) // before the start
    if (k === 0) await topup(w, S + 1000, 0.5)
    await trade(w, day + 5 * 3600_000, 100, prof[k])
    await topup(w, day + 9 * 3600_000, 0.5)
  }
}
await reset(W3, { start: S }); await feed(W3, 5)
for (let k = 0; k < 5; k++) { const run = Date.parse('2026-11-12T20:07:00Z') + k * DAY; await setSync(W3, run); await sync(run) }
const noTx = (x) => JSON.stringify({ ...x, entries: x.entries.map(({ tx: _, ...e }) => e) }) // the two feeds make different made-up hashes
const liveH = await snapshot()
await reset(W3, { start: S }); await feed(W3, 5)
const late = Date.parse('2026-11-17T09:00:00Z'); await setSync(W3, late)
const cH = await sync(late)
const catchH = await snapshot()
ok(noTx(liveH) === noTx(catchH) && cH.days_carved === 5, 'five days late: one run produces exactly the entries a daily run would have (carves, losses carried, gas in time order)')
const dh = catchH.days.map((x) => `${x.day.slice(8)}:${Number(x.carve).toFixed(2)}`).join(' ')
ok(dh === '12:1.00 13:0.00 14:0.00 15:0.40 16:2.00', 'carves: +$10 -> $1.00; -$6 and a quiet day -> 0; +$10 wins back the $6, 10% of $4 -> $0.40; +$20 -> $2.00', dh)
ok(catchH.entries.filter((e) => e.kind === 'gas').length === 6 && catchH.entries.find((e) => e.kind === 'gas').uncovered === '0.500000', 'six top-ups booked in time order; the first one (pot still empty at that moment) fell on members')
const potH = await pool()
console.log(`         catch-up after 5 days: pot $${potH.toFixed(2)} (the value posted next is lowered once by this amount)`)
const sn = await snapshot(); await sync(late + 600_000)
ok(JSON.stringify(await snapshot()) === JSON.stringify(sn), 'running it again changes nothing')

// =====================================================================================================
console.log('hand-made: a losing day')
await reset(W2, { start: T0 })
await setSync(W2, RUN(0)); await trade(W2, AT(0, 1), 100, 10)
let r = await carve(RUN(0)); ok(r.status === 'carved' && near(r.carve, 1), 'a day with +$10 carves $1.00')
await setSync(W2, RUN(1)); await trade(W2, AT(1, 1), 100, -6)
r = await carve(RUN(1)); ok(r.status === 'carved' && near(r.carve, 0), 'a day with -$6 carves nothing')
ok(near(await pool(), 1), 'the pot is untouched by the loss (it is not clawed back)')
await setSync(W2, RUN(2)); await trade(W2, AT(2, 1), 100, 10)
r = await carve(RUN(2)); ok(near(r.carve, 0.4), 'next day +$10: first wins back the $6, then 10% of the $4 above = $0.40')
ok(near(await pool(), 1.4), 'pot after three days: $1.40')
const lg = (await one(`select public.treasury_overview() o`)).o.log
ok(lg.some((l) => l.kind === 'carve' && l.amount === 0 && /won back/.test(l.note)), 'the losing day shows in the log with the reason')
await reset(W2, { start: T0, carry: false })
for (const [k, p] of [[0, 10], [1, -6], [2, 10]]) { await setSync(W2, RUN(k)); await trade(W2, AT(k, 1), 100, p); r = await carve(RUN(k)) }
ok(near(r.carve, 1) && near(await pool(), 2), 'with carry_losses off the third day carves the full 10% ($1.00)')

console.log('hand-made: two keeper runs in one day')
await reset(W2, { start: T0 })
await setSync(W2, RUN(0)); await trade(W2, AT(0, 1), 100, 20)
const a = await carve(RUN(0))
await trade(W2, AT(0, 20.3), 100, 5); await setSync(W2, RUN(0) + 3600_000)
const b = await carve(RUN(0) + 3600_000)
ok(a.status === 'carved' && b.status === 'already' && (await q(`select 1 from public.treasury_entries where kind = 'carve'`)).length === 1 && near(await pool(), 2), 'the second run the same day carves nothing')
await setSync(W2, RUN(1)); const c2 = await carve(RUN(1))
ok(near((await q(`select profit::text p from public.treasury_days where day = (select max(day) from public.treasury_days)`))[0].p, 5) && near(c2.carve, 0.5), 'the profit made after 20:00 is carved the next day, once')
await reset(W2, { start: T0 }); await setSync(W2, RUN(0)); await trade(W2, AT(0, 1), 100, 20)
{ // two real connections: the first holds its transaction open while the second tries to carve the same day
  const other = conn('treasury_test'); await other.connect()
  await db.query('begin'); const first = await db.query(`select public.treasury_carve(${iso(RUN(0))}) r`)
  const second = other.query(`select public.treasury_carve(${iso(RUN(0) + 5000)}) r`) // waits for the lock
  await new Promise((ok_) => setTimeout(ok_, 300)); await db.query('commit')
  const s2 = (await second).rows[0].r; await other.end()
  ok(first.rows[0].r.status === 'carved' && s2.status === 'already' && (await q(`select 1 from public.treasury_entries where kind = 'carve'`)).length === 1, 'two keeper runs at the very same moment (two connections): one carves, the other waits and finds it done')
}

console.log('hand-made: history not there yet, inactive treasury, nothing back-dated')
await reset(W2, { start: T0 }); await setSync(W2, AT(0, 19)) // history stops before the day ends
await trade(W2, AT(0, 1), 100, 10)
r = await carve(RUN(0)); ok(r.status === 'stale' && (await entries()).length === 0 && (await q(`select 1 from public.treasury_days`)).length === 0, 'the day has ended but the history has not reached 20:00: nothing is carved, the keeper settles later')
await setSync(W2, RUN(0)); r = await carve(RUN(0)); ok(r.status === 'carved', 'once the history reaches the end of the day it carves')
r = await carve(AT(0, 12)); ok(r.status === 'already', 'before a day has ended nothing is due')
await db.query(`update public.treasury_config set active = false`)
ok((await carve(T0)).status === 'inactive' && (await sync()).active === false, 'inactive: carve and sync do nothing')
ok((await one(`select public.treasury_status() s`)).s.active === false, 'inactive: the keeper is told to behave as before')
// start through the admin function: the stored start time is kept
await reset(W2, { start: 0 }); await db.query(`update public.treasury_config set active = false, wallet = null, start_at = default`)
await db.query(`update public.settings set bot_wallet = $1`, [W2.toUpperCase().replace('0X', '0x')])
await setSync(W2, Date.now() - 60_000)
await throws(() => asRole('authenticated', MEMBER, `select public.admin_treasury_start()`), /Admins only/, 'a member cannot start the treasury')
await throws(() => asRole('anon', null, `select public.admin_treasury_start()`), /permission denied/, 'a visitor cannot start the treasury')
const st1 = (await asRole('authenticated', ADMIN, `select public.admin_treasury_start() r`)).rows[0].r
const cfg = await one(`select *, floor(extract(epoch from start_at) * 1000)::bigint s from public.treasury_config`)
ok(cfg.active && cfg.wallet === W2 && Number(cfg.s) === DEFAULT_START && st1.counts_from, 'admin start: active, wallet taken from settings in lower case, still counting from the stored start time (not from now)')
await throws(() => asRole('authenticated', ADMIN, `select public.admin_treasury_start()`), /already running/, 'starting twice is refused')
await db.query(`update public.settings set bot_wallet = $1`, [BOT])
await db.query(`update public.treasury_config set active = false, wallet = null`)
await asRole('authenticated', ADMIN, `select public.admin_treasury_settings(null, null, null, null, '2026-11-12T00:00:00Z')`)
ok(Number((await one(`select floor(extract(epoch from start_at) * 1000)::bigint s from public.treasury_config`)).s) === Date.parse('2026-11-12T00:00:00Z'), 'the start time can be changed (while nothing has been booked)')
await db.query(`update public.treasury_config set start_at = default`)
await reset(W2, { start: T0 }); await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 1, 1, 1)`)
await throws(() => asRole('authenticated', ADMIN, `select public.admin_treasury_settings(null, null, null, null, '2026-11-12T00:00:00Z')`), /cannot change/, 'but not after the treasury has booked anything')

console.log('hand-made: gas top-ups')
await reset(W2, { start: T0 }); await setSync(W2, AT(1, 1))
await topup(W2, T0 + 1000, 0.5)
await sync(AT(1, 1))
let en = await entries()
ok(en.length === 1 && en[0].kind === 'gas' && near(en[0].amount, 0.5) && near(en[0].pot_delta, 0) && near(en[0].uncovered, 0.5) && (await pool()) === 0, 'a top-up while the pot is empty: logged in full, pot stays 0, $0.50 falls on members')
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms, note) values ('carve', 0.3, 0.3, $1, 'seed')`, [T0 + 2000])
await topup(W2, T0 + 3000, 0.5); await sync(AT(1, 1))
en = await entries()
ok(near(en[2].pot_delta, -0.3) && near(en[2].uncovered, 0.2) && near(await pool(), 0), 'pot holds $0.30, top-up $0.50: pot pays $0.30, members $0.20, pot ends at exactly 0')
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms, note) values ('carve', 5, 5, $1, 'seed')`, [T0 + 4000])
const h = await topup(W2, T0 + 5000, 0.5)
await sync(AT(1, 1)); await sync(AT(1, 1))
ok((await q(`select 1 from public.treasury_entries where tx = $1`, [h])).length === 1 && near(await pool(), 4.5), 'the same top-up seen twice (two syncs): recorded once, pot $4.50')
await db.query(`select public.pool_apply($1, 1, 100000, now(), $2::jsonb, true)`, [W2, JSON.stringify([{ hash: h, block: 105, tx_index: 0, t: T0 + 5000, sent: true, to: ROUTER, usdt: '-0.500000' }])]).catch(() => null)
await sync(AT(1, 1))
ok((await q(`select 1 from public.treasury_entries where tx = $1`, [h])).length === 1, 'the same top-up re-read from the chain (pool_apply again): still once')
await throws(() => db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms, tx) values ('gas', 0.5, -0.5, 1, $1)`, [h]), /duplicate key/, 'the database itself refuses a second entry for one transaction')
await tx(W2, { t: T0 + 6000, usdt: -0.5, to: OTHER_ROUTER }); await tx(W2, { t: T0 + 7000, usdt: -0.5, to: ROUTER, sent: false })
await tx(W2, { t: T0 + 8000, usdt: 0.5, to: ROUTER }); await tx(W2, { t: T0 - 5000, usdt: -0.5, to: ROUTER })
await sync(AT(1, 1)); ok((await q(`select 1 from public.treasury_entries where kind = 'gas'`)).length === 3, 'only the wallet\'s own small router buys after the start count as gas')

console.log('hand-made: the identity (members\' value = wallet - pot)')
await reset(W2, { start: T0 }); await setSync(W2, Date.now() + 5000)
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 5, 5, $1)`, [T0 + 1])
let wal = 100
const members0 = wal - 5
await topup(W2, T0 + 1000, 0.5); wal -= 0.5; await sync()
ok(near(wal - await pool(), members0), 'gas top-up with funds in the pot: members\' value unchanged ($95.00 before and after; the CELO bought is not counted)')
await db.query(`insert into public.treasury_entries (kind, status, amount, paid_on, dest, note) values ('render', 'pending', 3, current_date, $1, 'Render')`, [DEST])
await withdraw(W2, Date.now() - 1000, 3, DEST); wal -= 3; await sync()
ok(near(wal - await pool(), members0), 'Render withdrawal $3: wallet -$3, pot -$3, members\' value unchanged')
await db.query(`truncate public.treasury_entries restart identity`); await db.query(`delete from public.pool_tx where wallet = $1`, [W2])
wal = 100; await topup(W2, Date.now() - 5000, 0.5); wal -= 0.5; await sync()
ok(near(wal - (await pool()), 99.5), 'pot empty: the same top-up lowers members\' value by $0.50, exactly as gas does today')

console.log('Render withdrawals to an address: validation')
await reset(W2, { start: T0 }); await setSync(W2, Date.now() + 5000)
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 5, 5, $1)`, [T0 + 1])
const rec = (amt, note, o = {}) => asRole('authenticated', ADMIN, `select public.admin_treasury_record_render($1, coalesce($2::date, (now() at time zone 'utc')::date), $3, $4, $5, $6) r`, [amt, o.date ?? null, note, o.dest === undefined ? DEST : o.dest, o.tx ?? null, o.allow ?? false]).then((x) => x.rows[0].r)
await throws(() => rec(8, 'x', { dest: '0x' + '0'.repeat(40) }), /zero address/, 'the zero address is refused')
await throws(() => rec(8, 'x', { dest: USDT }), /USDT contract/, 'the USDT contract is refused')
await throws(() => rec(8, 'x', { dest: W2 }), /pool wallet itself/, 'the pool wallet itself is refused')
await throws(() => rec(8, 'x', { dest: VAULT }), /vault/, 'the fund vault is refused')
await throws(() => rec(8, 'x', { dest: '0x1234' }), /40 characters/, 'a short address is refused')
await throws(() => rec(8, 'x', { dest: null }), /40 characters/, 'no address is refused')
await throws(() => rec(8, 'x', { dest: 'zz' + 'd'.repeat(40) }), /40 characters/, 'a non-hex address is refused')
await throws(() => rec(20, 'too big'), /holds only \$5\.00/, 'a withdrawal larger than the pot is refused, with the numbers')
await throws(() => asRole('authenticated', MEMBER, `select public.admin_treasury_record_render(1, current_date, 'x', $1, null, false)`, [DEST]), /Admins only/, 'a member cannot record a withdrawal')
await throws(() => rec(0, 'x'), /amount/, 'zero is refused'); await throws(() => rec(8, 'x', { tx: '0x12' }), /64 characters/, 'a malformed transaction hash is refused')
await throws(() => rec(8, 'x', { date: '2030-01-01' }), /date/, 'a date far in the future is refused')
await throws(() => rec(5, 'x'.repeat(201)), /note is too long/, 'a very long note is refused')
ok((await entries()).filter((e) => e.kind === 'render').length === 0, 'none of the refused ones left anything behind')

console.log('Render withdrawals: confirmation by amount and recipient')
const p1 = await rec(20, 'Render, bigger than the pot', { allow: true })
ok(p1.matched === false && (await pool()) === 5, 'recorded with "allow": it waits, and the pot is unchanged until the transfer exists')
const stH = (await one(`select public.treasury_status(null, now()) s`)).s
ok(stH.hold === true && /not matched/.test(stH.hold_reason), 'while it waits the keeper is told to hold')
await withdraw(W2, Date.now() - 3000, 20, DEST2) // right amount, wrong recipient
await withdraw(W2, Date.now() - 2500, 20.5, DEST) // wrong amount
await tx(W2, { t: Date.now() - 2000, usdt: -20, to: USDT, sent: false, peer: DEST }) // not sent by the wallet
await withdraw(W2, Date.now() - 1500, 20, null) // old row: recipient not known
await sync(); ok((await q(`select 1 from public.treasury_entries where status = 'done' and kind = 'render'`)).length === 0, 'right amount to a different address, a different amount, a transfer the wallet did not send, and one with no recipient stored: none confirms it')
const unmatched = (await asRole('authenticated', ADMIN, `select public.admin_treasury_admin() a`)).rows[0].a.unmatched
ok(unmatched.length === 3 && unmatched.some((u) => u.peer === DEST2) && unmatched.some((u) => u.peer === null) && unmatched.every((u) => u.hash), 'the three transfers the wallet sent are listed to the admin as unexplained, with the address the chain shows (or none)')
ok((await pool()) === 5 && (await q(`select 1 from public.treasury_entries where kind = 'render' and status = 'done'`)).length === 0, 'unexplained transfers are not taken from the pot (and nothing is booked against members)')
const thash = await withdraw(W2, Date.now() - 1000, 20, DEST)
await sync()
en = (await entries()).find((x) => x.kind === 'render')
ok(en.status === 'done' && en.tx === thash && en.peer === DEST && near(en.pot_delta, -5) && near(en.uncovered, 15) && (await pool()) === 0, 'the transfer with the right amount to the right address confirms it: the pot paid its $5, members the other $15; pot is 0, never negative')
ok(/short/.test(en.note), 'the log says the pot was short')
const ovR = (await one(`select public.treasury_overview() o`)).o.log.find((l) => l.kind === 'render')
ok(ovR.peer === DEST && ovR.tx === thash, 'the log shows the destination the chain confirmed, with the transaction')
await sync(); ok((await q(`select 1 from public.treasury_entries where kind = 'render' and status = 'done'`)).length === 1, 'syncing again does not confirm it twice')
// two withdrawals of the same amount to the same address match two different transfers
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 30, 30, $1)`, [T0 + 2])
await rec(8, 'month 1'); await rec(8, 'month 2')
const t1h = await withdraw(W2, Date.now() + 1, 8, DEST); await setSync(W2, Date.now() + 9000); await sync()
let rs = (await q(`select * from public.treasury_entries where kind = 'render' and amount = 8 order by id`))
ok(rs[0].status === 'done' && rs[1].status === 'pending' && rs[0].tx === t1h, 'two $8 withdrawals, one transfer so far: one confirmed, one waits')
await withdraw(W2, Date.now() + 2, 8, DEST); await setSync(W2, Date.now() + 9000); await sync()
rs = (await q(`select * from public.treasury_entries where kind = 'render' and amount = 8 order by id`))
ok(rs.every((x) => x.status === 'done') && rs[0].tx !== rs[1].tx && near(await pool(), 14), 'the second transfer confirms the second withdrawal; pot 30 - 16 = $14')
await rec(8, 'to someone else', { dest: DEST2 }); await withdraw(W2, Date.now() + 3, 8, DEST); await setSync(W2, Date.now() + 9000); await sync()
ok((await q(`select 1 from public.treasury_entries where kind = 'render' and status = 'pending' and dest = $1`, [DEST2])).length === 1, 'a third $8 withdrawal to another address is not confirmed by a transfer to the first address')
const pid = (await q(`select id from public.treasury_entries where kind = 'render' and status = 'pending'`))[0].id
await asRole('authenticated', ADMIN, `select public.admin_treasury_cancel_render($1)`, [pid])
ok((await one(`select status from public.treasury_entries where id = $1`, [pid])).status === 'void', 'a typo can be cancelled while it waits')
await throws(() => asRole('authenticated', ADMIN, `select public.admin_treasury_cancel_render($1)`, [en.id]), /not waiting/, 'a confirmed withdrawal cannot be cancelled')

console.log('Render withdrawals: the transaction hash is checked against the chain row')
await reset(W2, { start: T0 }); await setSync(W2, Date.now() + 5000)
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 100, 100, $1)`, [T0 + 1])
const good = await withdraw(W2, Date.now() - 4000, 12, DEST), noPeer = await withdraw(W2, Date.now() - 3900, 13, null), viaRouter = await topup(W2, Date.now() - 3800, 0.5)
const mm = async (amount, dest, txh) => { const x = await rec(amount, 'x', { dest, tx: txh }); return { x, d: (await one(`select status, detail->>'mismatch' m from public.treasury_entries where id = $1`, [x.id])) } }
let m1 = await mm(12, DEST2, good); ok(m1.d.status === 'pending' && /went to 0xd+, not to 0xe+/.test(m1.d.m), 'hash given, other recipient than typed: not confirmed; says where the chain shows it went', m1.d.m)
m1 = await mm(11, DEST, good); ok(m1.d.status === 'pending' && /\$12\.00 in that transaction, not \$11\.00/.test(m1.d.m), 'hash given, other amount than typed: not confirmed; says the amounts', m1.d.m)
m1 = await mm(13, DEST, noPeer); ok(m1.d.status === 'pending' && /recipient .* not stored/.test(m1.d.m), 'hash given, recipient not stored yet: not confirmed, with the reason', m1.d.m)
m1 = await mm(0.5, DEST, viaRouter); ok(m1.d.status === 'pending' && /not a direct USDT transfer/.test(m1.d.m), 'hash of a gas top-up: not a direct transfer', m1.d.m)
m1 = await mm(12, DEST, H(999999)); ok(m1.d.status === 'pending' && /not in the wallet's history yet/.test(m1.d.m), 'hash not in the history yet: waits, says so', m1.d.m)
ok(near(await pool(), 99.5), 'none of those moved the pot (only the $0.50 gas top-up in the data was booked: 100 - 0.5)')
const adm0 = (await asRole('authenticated', ADMIN, `select public.admin_treasury_admin() a`)).rows[0].a
ok(adm0.pending.length === 5 && adm0.pending.every((p) => p.mismatch), 'the admin page lists each waiting withdrawal with its reason')
await db.query(`update public.treasury_entries set status = 'void' where status = 'pending'`)
const okR = await rec(12, 'paid', { dest: DEST, tx: good }); ok(okR.matched === true && near(await pool(), 87.5), 'hash given and everything agrees: confirmed at once, pot 99.50 - 12')
const dup = await rec(12, 'again', { dest: DEST, tx: good }); ok(dup.matched === false && /already recorded/.test(dup.mismatch), 'the same transaction cannot confirm a second withdrawal')

console.log('keeper hold for an unrecorded transfer')
await reset(W2, { start: T0 }); const now = Date.now(); await setSync(W2, now)
await withdraw(W2, now - 3_600_000, 8, DEST)
let st = (await one(`select public.treasury_status(null, now()) s`)).s
ok(st.hold && /not recorded/.test(st.hold_reason), 'USDT left in a direct transfer an hour ago and is not recorded: hold')
await db.query(`delete from public.pool_tx where wallet = $1`, [W2]); await withdraw(W2, now - 4 * 3_600_000, 8, DEST)
st = (await one(`select public.treasury_status(null, now()) s`)).s
ok(!st.hold, 'the same transfer four hours ago: no hold any more (it is treated as a real outflow, as today)')
st = (await one(`select public.treasury_status(1000, now()) s`)).s
ok(st.lag_blocks !== null && st.cursor_block !== null, 'status reports how far the history is behind the chain head')

console.log('hand-made: sweep (return the excess to members)')
await reset(W2, { start: T0 }); await setSync(W2, AT(1, 1))
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 100, 100, $1)`, [T0 + 1])
for (let i = 0; i < 14; i++) await topup(W2, Date.now() - (13 - i) * DAY, 0.5)
const o2 = (await one(`select public.treasury_overview() o`)).o
ok(o2.excess > 0 && near(o2.excess, 100 - o2.target_balance, 0.01), `overview: a $100 pot against $${o2.target_balance} for 60 days of costs: excess $${o2.excess}`)
await throws(() => asRole('authenticated', ADMIN, `select public.admin_treasury_sweep(10000)`), /most that can be returned/, 'returning more than the excess is refused')
const sw = (await asRole('authenticated', ADMIN, `select public.admin_treasury_sweep(null) r`)).rows[0].r
ok(sw.returned > 0 && near(await pool(), 100 - sw.returned) && (await q(`select 1 from public.treasury_entries where kind = 'sweep'`)).length === 1, 'the sweep is a logged entry; the pot goes down by it')
await throws(() => asRole('authenticated', ADMIN, `select public.admin_treasury_sweep(null)`), /most that can be returned/, 'a second sweep has nothing left to return')

console.log('constraints')
await throws(() => db.query(`insert into public.treasury_entries (kind, amount, pot_delta) values ('carve', 5, 4)`), /treasury_entries_shape/, 'a carve must add exactly its amount to the pot')
await throws(() => db.query(`insert into public.treasury_entries (kind, amount, pot_delta, uncovered) values ('gas', 1, -0.5, 0.2)`), /treasury_entries_shape/, 'gas must split exactly into pot + members')
await throws(() => db.query(`insert into public.treasury_entries (kind, amount, pot_delta) values ('gas', 1, 1)`), /treasury_entries_shape/, 'gas can never add to the pot')
await throws(() => db.query(`insert into public.treasury_entries (kind, status, amount) values ('gas', 'pending', 1)`), /treasury_entries_shape/, 'only a Render withdrawal can wait')
await throws(() => db.query(`insert into public.treasury_entries (kind, amount, tx) values ('render', 1, '0xABC')`), /check/, 'a malformed hash is refused')
await throws(() => db.query(`insert into public.treasury_entries (kind, status, amount) values ('render', 'pending', 1)`), /render_dest/, 'a Render withdrawal always has a destination')
await throws(() => db.query(`insert into public.treasury_entries (kind, status, amount, dest, peer, pot_delta) values ('render', 'done', 1, $1, $2, -1)`, [DEST, DEST2]), /render_peer/, 'a confirmed Render withdrawal always went where it was recorded')
await throws(() => db.query(`insert into public.pool_tx (wallet, hash, block, tx_index, t, sent, to_addr, usdt, peer) values ('w', $1, 1, 0, 1, true, '', 1, '0xABC')`, [H(5)]), /pool_tx_peer_shape/, 'pool_tx.peer must be a lower-case address')

// =====================================================================================================
console.log('keeper decision code (_shared/treasury.ts)')
const T = await import(`${SQL}/functions/_shared/treasury.ts`)
const M = (x) => BigInt(Math.round(x * 1e6))
const act = { active: true, wallet: BOT, balance: 21.11, hold: false, hold_reason: null, lag_blocks: 10 }
let d = T.postedValue(M(590), act, BOT.toUpperCase().replace('0X', '0x')); ok(d.kind === 'value' && d.micro === M(590 - 21.11), 'posts wallet value minus the pot')
d = T.postedValue(M(590), { active: false }, BOT); ok(d.kind === 'plain', 'treasury off: posts the wallet value as before')
d = T.postedValue(M(590), act, '0x' + '9'.repeat(40)); ok(d.kind === 'plain', 'a different wallet than the pot\'s: the pot does not apply')
d = T.postedValue(M(590), { ...act, hold: true, hold_reason: 'x' }, BOT); ok(d.kind === 'skip', 'hold: posts nothing')
d = T.postedValue(M(590), { ...act, lag_blocks: 500 }, BOT); ok(d.kind === 'skip', 'history far behind the chain: posts nothing')
d = T.postedValue(M(590), { ...act, lag_blocks: null }, BOT); ok(d.kind === 'skip', 'unknown lag: posts nothing')
d = T.postedValue(M(10), act, BOT); ok(d.kind === 'skip', 'pot bigger than the wallet: posts nothing rather than a nonsense value')
d = T.postedValue(M(590), null, BOT); ok(d.kind === 'skip', 'treasury status unreadable: posts nothing (never the wallet value with the pot still inside)')
d = T.postedValue(M(590.004999), { ...act, balance: 0 }, BOT); ok(d.kind === 'value' && d.micro === M(590.004999), 'an empty pot changes nothing')
ok(T.isNotInstalled({ code: 'PGRST202' }) && T.isNotInstalled({ code: '42883' }) && !T.isNotInstalled({ code: '57014' }), 'a database without 014 yet is recognised (the keeper behaves as before); any other error is not')
const fake = (script) => ({ rpc: async (name, args) => { const f = script[name]; return typeof f === 'function' ? f(args) : f ?? { data: null, error: { code: 'PGRST202' } } } })
let called = []
let res = await T.treasuryValue(fake({}), { due: true, forWallet: BOT, head: async () => 1n, wallet: async () => M(590), refresh: async () => {} })
ok(res.kind === 'plain' && res.micro === M(590), 'runner: treasury not installed yet: wallet value, as before')
const okDb = (carveStatus) => fake({
  treasury_status: (a) => ({ data: { ...act, lag_blocks: a?.p_head_block === undefined ? null : 5 }, error: null }),
  treasury_sync: () => { called.push('sync'); return { data: {}, error: null } },
  treasury_carve: () => { called.push('carve'); return { data: { status: carveStatus }, error: null } },
})
called = []; res = await T.treasuryValue(okDb('carved'), { due: true, forWallet: BOT, head: async () => 1000n, wallet: async () => M(590), refresh: async () => called.push('refresh') })
ok(res.kind === 'value' && !res.carvePending && called[0] === 'refresh' && called.indexOf('carve') > called.indexOf('sync'), 'runner: refreshes the history, syncs, carves (due), then values the wallet')
called = []; res = await T.treasuryValue(okDb('stale'), { due: true, forWallet: BOT, head: async () => 1000n, wallet: async () => M(590), refresh: async () => {} })
ok(res.kind === 'value' && res.carvePending === true, 'runner: carve could not run (history not there yet): value still posted, but the settlement must wait')
called = []; res = await T.treasuryValue(okDb('carved'), { due: false, forWallet: BOT, head: async () => 1000n, wallet: async () => M(590), refresh: async () => {} })
ok(!called.includes('carve'), 'runner: no carve outside the settlement run')
res = await T.treasuryValue(fake({ treasury_status: { data: act, error: null }, treasury_sync: { data: null, error: { code: 'XX000', message: 'boom' } }, treasury_carve: { data: { status: 'carved' }, error: null } }), { due: true, forWallet: BOT, head: async () => 1n, wallet: async () => M(590), refresh: async () => {} })
ok(res.kind === 'skip', 'runner: the pot cannot be brought up to date: posts nothing')

await db.end()
console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
