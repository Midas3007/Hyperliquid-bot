// Tests for 014_treasury.sql on a throwaway local Postgres (never the live project).
//   node treasury.test.mjs
// It creates its own database `treasury_test` (dropped and rebuilt every run), loads the real 009 and 013 migrations,
// copies the real season rows of the bot wallet out of the local `postgres` database (read only), runs 014 twice,
// and checks the accounting on the real rows and on hand-made cases. Also checks the keeper's pure decision code.
// Start Postgres first (see the fund-site README / the task notes) and set PG_MODULE if `pg` is not installed here.
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SQL = resolve(HERE, '..')
const require = createRequire(process.env.PG_MODULE ? process.env.PG_MODULE : import.meta.url)
let pg
try { pg = require('pg') } catch { pg = createRequire('/tmp/claude-0/-home-user/3a8d6223-ebc9-5af4-ae9c-0452ac68f60b/scratchpad/pg/package.json')('pg') }
const conn = (database) => new pg.Client({ host: '127.0.0.1', port: 54329, user: 'postgres', database })
const BOT = '0x6cd37bc290c5c02ea92086d416a6a673bca581dc'
const USDT = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
const ROUTER = '0x5615cdab10dc425a742d643d949a7f474c01abc4'
const DAY = 86_400_000

let pass = 0, fail = 0
const ok = (cond, name, extra = '') => { if (cond) { pass++; console.log('  ok   ', name) } else { fail++; console.log('  FAIL ', name, extra) } }
const near = (a, b, eps = 1e-5) => Math.abs(Number(a) - Number(b)) <= eps
const throws = async (fn, re, name) => { try { await fn(); ok(false, name, 'did not throw') } catch (e) { ok(re.test(e.message), name, e.message) } }

// ---------- build the database ----------
const root = conn('postgres'); await root.connect()
const src = conn('postgres'); await src.connect() // read only: the real season rows
await root.query('drop database if exists treasury_test'); await root.query('create database treasury_test'); await root.end()
const db = conn('treasury_test'); await db.connect()
await db.query(`
  do $$ begin create role anon; exception when duplicate_object then null; end $$;
  do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
  do $$ begin create role service_role; exception when duplicate_object then null; end $$;
  grant usage on schema public to anon, authenticated, service_role;
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
  create schema cron; create schema net; create schema auth;
  create table cron.job (jobname text primary key, schedule text, command text);
  create table cron.job_run_details (end_time timestamptz);
  create function cron.schedule(n text, s text, c text) returns bigint language sql as $f$ insert into cron.job values (n, s, c) on conflict (jobname) do update set schedule = excluded.schedule, command = excluded.command returning 1::bigint $f$;
  create function cron.unschedule(n text) returns boolean language plpgsql as $f$ begin delete from cron.job where jobname = n; if not found then raise exception 'could not find valid entry for job'; end if; return true; end $f$;
  create function auth.uid() returns uuid language sql stable as $f$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $f$;
  grant usage on schema auth to anon, authenticated, service_role;
  create table public.settings (id int primary key, bot_wallet text, fund_name text);
  insert into public.settings values (1, '${BOT}', 'test');
  create table public.members (user_id uuid, is_admin boolean not null default false);
  insert into public.members values ('00000000-0000-0000-0000-0000000000aa', true), ('00000000-0000-0000-0000-0000000000bb', false);
  create function public.is_admin() returns boolean language sql stable security definer set search_path = '' as $f$ select exists (select 1 from public.members where user_id = auth.uid() and is_admin) $f$;
`)
await db.query(readFileSync(`${SQL}/009_alerts.sql`, 'utf8').replace(/select net\.http_post[\s\S]*?\$cron\$\);/, 'select 1 $cron$);'))
await db.query(readFileSync(`${SQL}/013_pool_tx.sql`, 'utf8'))
// the real rows (select only on the source)
const realTx = (await src.query(`select * from public.pool_tx where wallet = $1 order by block, tx_index`, [BOT])).rows
const realSync = (await src.query(`select * from public.pool_sync where wallet = $1`, [BOT])).rows[0]
await src.end()
await db.query(`insert into public.pool_sync (wallet, from_block, block, block_time, since_ms, synced_at) values ($1,$2,$3,$4,$5,$6)`,
  [BOT, realSync.from_block, realSync.block, realSync.block_time, realSync.since_ms, realSync.synced_at])
await db.query(`insert into public.pool_tx (wallet, hash, block, tx_index, t, sent, to_addr, usdt)
  select $1, x->>'hash', (x->>'block')::bigint, (x->>'tx_index')::int, (x->>'t')::bigint, (x->>'sent')::boolean, x->>'to_addr', (x->>'usdt')::numeric from jsonb_array_elements($2::jsonb) x`,
  [BOT, JSON.stringify(realTx)])
const SINCE = Number(realSync.since_ms)
const CURSOR = new Date(realSync.block_time).getTime()

const mig = readFileSync(`${SQL}/014_treasury.sql`, 'utf8')
console.log('migration')
await db.query(mig); ok(true, 'runs on a database with the real 013 tables')
await db.query(mig); ok(true, 'runs a second time (safe to repeat)')
const jobs = (await db.query(`select jobname, schedule, command from cron.job where jobname = 'treasury-sync'`)).rows
ok(jobs.length === 1 && jobs[0].schedule === '*/5 * * * *' && /treasury_sync/.test(jobs[0].command), 'the 5-minute schedule exists once')
ok((await db.query(`select count(*)::int n from public.treasury_config`)).rows[0].n === 1, 'one settings row after two runs')

console.log('permissions')
const priv = async (role, what) => (await db.query(`select ${what} as p`, [role])).rows[0].p
const fn = (sig) => `has_function_privilege($1, '${sig}', 'execute')`
for (const f of ['public.treasury_sync()', 'public.treasury_carve(timestamptz)', 'public.treasury_status(bigint,timestamptz)', 'public._treasury_balance()', 'public._treasury_profit(text,bigint,bigint)']) {
  ok(!(await priv('anon', fn(f))) && !(await priv('authenticated', fn(f))) && (await priv('service_role', fn(f))), `${f}: service role only`)
}
ok((await priv('anon', fn('public.treasury_overview()'))) && (await priv('authenticated', fn('public.treasury_overview()'))), 'members and visitors can read the overview')
for (const f of ['public.admin_treasury_start()', 'public.admin_treasury_record_render(numeric,date,text,text,boolean)', 'public.admin_treasury_sweep(numeric)', 'public.admin_treasury_cancel_render(bigint)', 'public.admin_treasury_settings(numeric,boolean,numeric,integer)', 'public.admin_treasury_admin()']) {
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
const reset = async (wallet, o = {}) => {
  await db.query(`truncate public.treasury_entries, public.treasury_days restart identity`)
  await db.query(`delete from public.pool_tx where wallet = $1`, [wallet === BOT ? '0x' + '0'.repeat(40) : wallet]) // hand-made wallets start empty; the real rows stay
  await db.query(`update public.treasury_config set active = true, wallet = $1, started_ms = $2, carve_pct = 0.10, carry_losses = $3, hold_minutes = 180 where id = 1`, [wallet, o.start ?? 0, o.carry ?? true])
}
const W2 = '0x' + '2'.repeat(40)
const setSync = async (wallet, ms, since = 0) => {
  await db.query(`insert into public.pool_sync (wallet, from_block, block, block_time, since_ms, synced_at) values ($1, 1, $2, to_timestamp($3 / 1000.0), $4, now())
    on conflict (wallet) do update set block = excluded.block, block_time = excluded.block_time, synced_at = excluded.synced_at, since_ms = excluded.since_ms`, [wallet, Math.floor(ms / 1000), ms, since])
}
let seq = 100
const tx = async (wallet, o) => { // one row of pool_tx
  const n = ++seq
  await db.query(`insert into public.pool_tx (wallet, hash, block, tx_index, t, sent, to_addr, usdt) values ($1,$2,$3,0,$4,$5,$6,$7)
    on conflict (wallet, hash) do update set usdt = excluded.usdt`, [wallet, o.hash ?? H(n), o.block ?? n, o.t, o.sent ?? true, o.to ?? '0xa9aa0a64769cbed4d3b1ceb4df01cde915c235b3', o.usdt])
  return o.hash ?? H(n)
}
const trade = async (w, t, size, profit) => { await tx(w, { t, usdt: -size }); await tx(w, { t: t + 1000, usdt: size + profit }) } // buy then sale
const topup = (w, t, amt = 0.5, hash) => tx(w, { t, usdt: -amt, to: ROUTER, hash })
const pool = async () => Number((await one(`select public._treasury_balance() b`)).b)
const carve = async (ms) => (await one(`select public.treasury_carve(to_timestamp($1 / 1000.0)) r`, [ms])).r
const sync = async () => (await one(`select public.treasury_sync() r`)).r
const entries = () => q(`select * from public.treasury_entries order by id`)
const T0 = Math.floor((Date.now() - 30 * DAY) / DAY) * DAY // hand-made cases start 30 days ago (midnight UTC)

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
await db.query(`update public.treasury_config set wallet = $1 where id = 1`, [BOT])
const gross = await one(`select public._treasury_profit($1, 0, $2) j`, [BOT, CURSOR + DAY])
ok(near(gross.j.profit, 348.428575, 1e-6) && gross.j.trades === 3144, 'treasury profit (top-ups left out) is $348.43 over the same 3,144 trades', JSON.stringify(gross.j))
ok(near(Number(gross.j.profit) - net, 13.730151, 1e-6), 'the difference is exactly the top-ups')

// =====================================================================================================
console.log('the whole season, replayed day by day (hypothetical start at the season start)')
await reset(BOT, { start: SINCE })
const days = []
for (let d = Date.parse('2026-10-29T20:07:00Z'); d < CURSOR; d += DAY) days.push(d)
days.push(CURSOR + 60_000) // the last carve: at the real latest history
let minPot = Infinity, maxCarve = 0
for (const d of days) {
  const asOf = Math.min(d, CURSOR)
  await db.query(`update public.pool_sync set block_time = to_timestamp($1 / 1000.0) where wallet = $2`, [asOf, BOT])
  await sync()
  const r = await carve(asOf + 30_000)
  maxCarve = Math.max(maxCarve, Number(r.carve ?? 0))
  minPot = Math.min(minPot, await pool())
}
const sum = async (k, col = 'amount') => Number((await one(`select coalesce(sum(${col}),0) s from public.treasury_entries where kind = $1 and status = 'done'`, [k])).s)
const carved = await sum('carve'), gasAll = await sum('gas'), gasFromPot = -(await sum('gas', 'pot_delta')), unc = Number((await one(`select coalesce(sum(uncovered),0) s from public.treasury_entries`)).s)
const potNow = await pool()
const dayRows = await q(`select * from public.treasury_days order by day`)
ok(dayRows.length === days.length, `one carve row per day (${dayRows.length} days)`)
ok(dayRows.every((r, i) => i === 0 ? Number(r.win_from) === SINCE : Number(r.win_from) === Number(dayRows[i - 1].win_to)), 'every window starts exactly where the last one ended (no gap, no overlap)')
ok(dayRows.reduce((s, r) => s + r.trades, 0) === 3144, 'the windows hold all 3,144 trades, each in exactly one window')
ok(near(dayRows.reduce((s, r) => s + Number(r.profit), 0), 348.428575, 1e-5), 'window profits add up to $348.43')
ok(near(carved, 0.1 * 348.428575, 2e-5), `carved in total = 10% of $348.43 = $${carved.toFixed(4)}`)
ok(near(gasAll, 13.730151, 1e-9), 'all 28 top-ups were logged once: $13.73')
ok(near(gasFromPot + unc, 13.730151, 1e-9), `gas = paid by the pot ($${gasFromPot.toFixed(2)}) + paid by members ($${unc.toFixed(2)})`)
ok(near(potNow, carved - gasFromPot, 1e-9) && potNow >= 0 && minPot >= 0, `the pot never went below zero; it would hold $${potNow.toFixed(2)} today`)
const wallet = net // wallet USDT change from closed trades, gas included
const members = wallet - potNow
ok(near(members, 0.9 * 348.428575 - unc, 5e-5), `members' value = wallet - pot = 90% of gross profit minus gas the pot could not cover ($${members.toFixed(2)})`)
ok(maxCarve / 590 < 0.03, `the biggest single carve is $${maxCarve.toFixed(2)}, ${(100 * maxCarve / 590).toFixed(2)}% of a $590 wallet: nowhere near the contract's 50% daily limit`)
console.log(`         first-day gas paid by members (pot still empty): $${unc.toFixed(2)}; gas per day (last 14 d): $${Number((await one(`select public._treasury_gas_per_day(to_timestamp($1 / 1000.0)) g`, [CURSOR])).g).toFixed(3)}`)
const e1 = (await entries()).length
await sync(); await carve(CURSOR + 120_000)
ok((await entries()).length === e1 && (await q(`select 1 from public.treasury_days`)).length === days.length, 'running sync and carve again changes nothing')
const ov = (await one(`select public.treasury_overview() o`)).o
ok(near(ov.balance, potNow, 1e-6) && ov.log.length > 40 && ov.log.length <= 100 && ov.log[0].t >= ov.log[1].t, 'overview: balance matches, log is newest first (at most 100 lines)')
ok(!JSON.stringify(ov).includes(BOT) && !('wallet' in ov) && !('unmatched' in ov), 'overview shows no wallet address and no admin data')
if (process.env.TREASURY_OVERVIEW_OUT) (await import('node:fs')).writeFileSync(process.env.TREASURY_OVERVIEW_OUT, JSON.stringify(ov, null, 1)) // for the screenshots
console.log(`         overview: balance $${Number(ov.balance).toFixed(2)}, ${ov.days_covered} days of costs covered (costs $${ov.cost_per_day}/day), full at $${ov.target_balance}`)

// =====================================================================================================
console.log('hand-made: a losing day')
await reset(W2, { start: T0 })
await setSync(W2, T0 + DAY / 2)
await trade(W2, T0 + 1000, 100, 10)
let r = await carve(T0 + DAY / 2); ok(r.status === 'carved' && near(r.carve, 1), 'a day with +$10 carves $1.00')
await setSync(W2, T0 + 1.5 * DAY); await trade(W2, T0 + DAY + 1000, 100, -6)
r = await carve(T0 + 1.5 * DAY); ok(r.status === 'carved' && r.carve === 0 && near(r.profit, -6), 'a day with -$6 carves nothing')
ok(near(await pool(), 1), 'the pot is untouched by the loss (it is not clawed back)')
await setSync(W2, T0 + 2.5 * DAY); await trade(W2, T0 + 2 * DAY + 1000, 100, 10)
r = await carve(T0 + 2.5 * DAY); ok(near(r.carve, 0.4) && near(r.carry_in, -6), 'next day +$10: first wins back the $6, then 10% of the $4 above = $0.40')
ok(near(await pool(), 1.4), 'pot after three days: $1.40')
let lg = (await one(`select public.treasury_overview() o`)).o.log
ok(lg.some((l) => l.kind === 'carve' && l.amount === 0 && /won back/.test(l.note)), 'the losing day shows in the log with the reason')
await reset(W2, { start: T0, carry: false })
await setSync(W2, T0 + DAY / 2); await trade(W2, T0 + 1000, 100, 10); await carve(T0 + DAY / 2)
await setSync(W2, T0 + 1.5 * DAY); await trade(W2, T0 + DAY + 1000, 100, -6); await carve(T0 + 1.5 * DAY)
await setSync(W2, T0 + 2.5 * DAY); await trade(W2, T0 + 2 * DAY + 1000, 100, 10); r = await carve(T0 + 2.5 * DAY)
ok(near(r.carve, 1) && near(await pool(), 2), 'with carry_losses off the third day carves the full 10% ($1.00)')

console.log('hand-made: two keeper runs in one day')
await reset(W2, { start: T0 })
await setSync(W2, T0 + 20.2 * 3600_000); await trade(W2, T0 + 3600_000, 100, 20)
const a = await carve(T0 + 20.2 * 3600_000)
await trade(W2, T0 + 20.3 * 3600_000, 100, 5); await setSync(W2, T0 + 21.2 * 3600_000)
const b = await carve(T0 + 21.2 * 3600_000)
ok(a.status === 'carved' && b.status === 'already' && (await q(`select 1 from public.treasury_entries where kind = 'carve'`)).length === 1 && near(await pool(), 2), 'the second run the same day carves nothing')
await setSync(W2, T0 + DAY + 20.2 * 3600_000)
const c2 = await carve(T0 + DAY + 20.2 * 3600_000)
ok(near(c2.profit, 5) && near(c2.carve, 0.5), 'the profit made between the two runs is carved the next day, once')
await reset(W2, { start: T0 })
await setSync(W2, T0 + 20.2 * 3600_000); await trade(W2, T0 + 3600_000, 100, 20)
{ // two real connections: the first holds its transaction open while the second tries to carve the same day
  const other = conn('treasury_test'); await other.connect()
  await db.query('begin'); const first = await db.query(`select public.treasury_carve(to_timestamp($1 / 1000.0)) r`, [T0 + 20.2 * 3600_000])
  const second = other.query(`select public.treasury_carve(to_timestamp($1 / 1000.0)) r`, [T0 + 20.2 * 3600_000 + 5000]) // waits for the lock
  await new Promise((ok_) => setTimeout(ok_, 300)); await db.query('commit')
  const s2 = (await second).rows[0].r; await other.end()
  ok(first.rows[0].r.status === 'carved' && s2.status === 'already' && (await q(`select 1 from public.treasury_entries where kind = 'carve'`)).length === 1, 'two keeper runs at the very same moment (two connections): one carves, the other waits and finds it done')
}

console.log('hand-made: stale history, inactive treasury, nothing back-dated')
await reset(W2, { start: T0 }); await setSync(W2, T0 + 3600_000)
await trade(W2, T0 + 1000, 100, 10)
r = await carve(T0 + 3600_000 + 16 * 60_000); ok(r.status === 'stale' && (await entries()).length === 0 && (await q(`select 1 from public.treasury_days`)).length === 0, 'history older than 15 minutes: nothing is carved, nothing is recorded')
r = await carve(T0 + 3600_000 + 14 * 60_000); ok(r.status === 'carved', 'the same day, once the history is fresh, it carves')
await db.query(`update public.treasury_config set active = false`)
ok((await carve(T0)).status === 'inactive' && (await sync()).active === false, 'inactive: carve and sync do nothing')
const st0 = (await one(`select public.treasury_status() s`)).s
ok(st0.active === false, 'inactive: the keeper is told to behave as before')
// start through the admin function: from zero, earlier trades are not back-carved
await reset(W2, { start: 0 }); await db.query(`update public.treasury_config set active = false, wallet = null, started_ms = null`)
await db.query(`update public.settings set bot_wallet = $1`, [W2.toUpperCase().replace('0X', '0x')])
await setSync(W2, Date.now() - 60_000); await trade(W2, Date.now() - 3_600_000, 100, 50)
await throws(() => asRole('authenticated', MEMBER, `select public.admin_treasury_start()`), /Admins only/, 'a member cannot start the treasury')
await throws(() => asRole('anon', null, `select public.admin_treasury_start()`), /permission denied/, 'a visitor cannot start the treasury')
await asRole('authenticated', ADMIN, `select public.admin_treasury_start()`)
const cfg = await one(`select * from public.treasury_config`)
ok(cfg.active && cfg.wallet === W2 && Math.abs(Number(cfg.started_ms) - Date.now()) < 60_000, 'admin start: active, wallet taken from settings in lower case, start time is now')
await throws(() => asRole('authenticated', ADMIN, `select public.admin_treasury_start()`), /already running/, 'starting twice is refused')
ok((await pool()) === 0, 'the pot starts at zero')
await setSync(W2, Date.now() + 1000)
r = await carve(Date.now() + 1000); ok(r.status === 'carved' && r.carve === 0 && r.trades === undefined && near(r.profit, 0), 'the first carve ignores the earlier $50 trade: nothing is back-carved')

console.log('hand-made: gas top-ups')
await reset(W2, { start: T0 }); await setSync(W2, T0 + DAY)
await topup(W2, T0 + 1000, 0.5)
await sync()
let en = await entries()
ok(en.length === 1 && en[0].kind === 'gas' && near(en[0].amount, 0.5) && near(en[0].pot_delta, 0) && near(en[0].uncovered, 0.5) && (await pool()) === 0, 'a top-up while the pot is empty: logged in full, pot stays 0, $0.50 falls on members')
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms, note) values ('carve', 0.3, 0.3, $1, 'seed')`, [T0 + 2000])
await topup(W2, T0 + 3000, 0.5); await sync()
en = await entries()
ok(near(en[2].pot_delta, -0.3) && near(en[2].uncovered, 0.2) && near(await pool(), 0), 'pot holds $0.30, top-up $0.50: pot pays $0.30, members $0.20, pot ends at exactly 0')
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms, note) values ('carve', 5, 5, $1, 'seed')`, [T0 + 4000])
const h = await topup(W2, T0 + 5000, 0.5)
await sync(); await sync()
ok((await q(`select 1 from public.treasury_entries where tx = $1`, [h])).length === 1 && near(await pool(), 4.5), 'the same top-up seen twice (two syncs): recorded once, pot $4.50')
await db.query(`select public.pool_apply($1, 1, 100000, now(), $2::jsonb, true)`, [W2, JSON.stringify([{ hash: h, block: 105, tx_index: 0, t: T0 + 5000, sent: true, to: ROUTER, usdt: '-0.500000' }])]).catch(() => null)
await sync()
ok((await q(`select 1 from public.treasury_entries where tx = $1`, [h])).length === 1, 'the same top-up re-read from the chain (pool_apply again): still once')
await throws(() => db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms, tx) values ('gas', 0.5, -0.5, 1, $1)`, [h]), /duplicate key/, 'the database itself refuses a second entry for one transaction')
await tx(W2, { t: T0 + 6000, usdt: -0.5, to: '0xa9aa0a64769cbed4d3b1ceb4df01cde915c235b3' }) // a different router: not gas
await tx(W2, { t: T0 + 7000, usdt: -0.5, to: ROUTER, sent: false }) // not sent by the wallet: not gas
await tx(W2, { t: T0 + 8000, usdt: 0.5, to: ROUTER }) // money in: not gas
await tx(W2, { t: T0 - 5000, usdt: -0.5, to: ROUTER }) // before the start: not counted
await sync(); ok((await q(`select 1 from public.treasury_entries where kind = 'gas'`)).length === 3, 'only the wallet\'s own small router buys after the start count as gas')
// the identity
console.log('hand-made: the identity (members\' value = wallet - pot)')
await reset(W2, { start: T0 }); await setSync(W2, Date.now() + 5000)
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 5, 5, $1)`, [T0 + 1])
let wal = 100
const V = () => wal - 0 // placeholder
const members0 = wal - 5
await topup(W2, T0 + 1000, 0.5); wal -= 0.5; await sync()
ok(near(wal - await pool(), members0), 'gas top-up with funds in the pot: members\' value unchanged ($95.00 before and after; the CELO bought is not counted)')
await db.query(`insert into public.treasury_entries (kind, status, amount, paid_on, note) values ('render', 'pending', 3, current_date, 'Render')`)
await tx(W2, { t: Date.now() - 1000, usdt: -3, to: USDT }); wal -= 3; await sync()
ok(near(wal - await pool(), members0), 'Render withdrawal $3: wallet -$3, pot -$3, members\' value unchanged')
await db.query(`update public.treasury_entries set pot_delta = 0, uncovered = 0 where false`)
await db.query(`truncate public.treasury_entries restart identity`); await setSync(W2, T0 + DAY)
wal = 100; await topup(W2, T0 + 9000, 0.5); wal -= 0.5; await sync()
ok(near(wal - (await pool()), 99.5), 'pot empty: the same top-up lowers members\' value by $0.50, exactly as gas does today')

console.log('hand-made: Render withdrawals')
await reset(W2, { start: T0 }); await setSync(W2, T0 + DAY)
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 5, 5, $1)`, [T0 + 1])
const rec = (amt, note, allow = false, txh = null, date = null) => asRole('authenticated', ADMIN, `select public.admin_treasury_record_render($1, coalesce($2::date, (now() at time zone 'utc')::date), $3, $4, $5) r`, [amt, date, note, txh, allow]).then((x) => x.rows[0].r)
await throws(() => rec(20, 'too big'), /holds only \$5\.00/, 'a withdrawal larger than the pot is refused with a clear message')
await throws(() => asRole('authenticated', MEMBER, `select public.admin_treasury_record_render(1, current_date, 'x', null, false)`), /Admins only/, 'a member cannot record a withdrawal')
await throws(() => rec(0, 'x'), /amount/, 'zero is refused'); await throws(() => rec(8, 'x', false, '0x12'), /64 characters/, 'a malformed transaction hash is refused')
await throws(() => rec(8, 'x', false, null, '2030-01-01'), /date/, 'a date far in the future is refused')
await throws(() => rec(5, 'x'.repeat(201)), /note is too long/, 'a very long note is refused')
const p1 = await rec(20, 'Render, bigger than the pot', true)
ok(p1.matched === false && (await pool()) === 5, 'recorded with "allow": it waits, and the pot is unchanged until the transfer exists')
const stH = (await one(`select public.treasury_status(null, now()) s`)).s
ok(stH.hold === true && /not matched/.test(stH.hold_reason), 'while it waits the keeper is told to hold')
await tx(W2, { t: Date.now() - 1000, usdt: -20, to: USDT, sent: false }) // a deposit-like row, not sent by the wallet
await tx(W2, { t: Date.now() - 1000, usdt: -20.5, to: USDT }) // wrong amount
await sync(); ok((await q(`select 1 from public.treasury_entries where status = 'done' and kind = 'render'`)).length === 0, 'a transfer of a different amount, or one the wallet did not send, does not match')
await setSync(W2, Date.now() + 5000)
const thash = await tx(W2, { t: Date.now(), usdt: -20, to: USDT }); await setSync(W2, Date.now() + 5000); await sync()
en = (await entries()).find((x) => x.kind === 'render')
ok(en.status === 'done' && en.tx === thash && near(en.pot_delta, -5) && near(en.uncovered, 15) && (await pool()) === 0, 'the real transfer appears: matched; the pot paid its $5, members the other $15; pot is 0, never negative')
ok(/short/.test(en.note), 'the log says the pot was short')
await sync(); ok((await q(`select 1 from public.treasury_entries where kind = 'render' and status = 'done'`)).length === 1, 'syncing again does not match it twice')
// two withdrawals of the same amount match two different transfers
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 30, 30, $1)`, [T0 + 2])
await rec(8, 'month 1'); await rec(8, 'month 2')
const t1 = await tx(W2, { t: Date.now() + 1, usdt: -8, to: USDT }); await setSync(W2, Date.now() + 9000); await sync()
let rs = (await q(`select * from public.treasury_entries where kind = 'render' and amount = 8 order by id`))
ok(rs[0].status === 'done' && rs[1].status === 'pending' && rs[0].tx === t1, 'two $8 withdrawals, one transfer so far: one matched, one waits')
const t2 = await tx(W2, { t: Date.now() + 2, usdt: -8, to: USDT }); await setSync(W2, Date.now() + 9000); await sync()
rs = (await q(`select * from public.treasury_entries where kind = 'render' and amount = 8 order by id`))
ok(rs.every((x) => x.status === 'done') && rs[0].tx !== rs[1].tx && near(await pool(), 14), 'the second transfer matches the second withdrawal; pot 30 - 16 = $14')
// a transfer sent BEFORE it was recorded (up to a week) still matches; with the hash given it must be that transaction
const old = await tx(W2, { t: Date.now() - 2 * DAY, usdt: -8.25, to: USDT }); await setSync(W2, Date.now() + 9000)
await rec(8.25, 'sent first, recorded later', false, old); ok((await q(`select 1 from public.treasury_entries where tx = $1 and status = 'done'`, [old])).length === 1, 'a transfer made two days before it was recorded is matched at once (hash given)')
// cancel
const pend = await rec(1, 'typo'); await asRole('authenticated', ADMIN, `select public.admin_treasury_cancel_render($1)`, [pend.id])
ok((await one(`select status from public.treasury_entries where id = $1`, [pend.id])).status === 'void', 'a typo can be cancelled while it waits')
await throws(() => asRole('authenticated', ADMIN, `select public.admin_treasury_cancel_render($1)`, [en.id]), /not waiting/, 'a matched withdrawal cannot be cancelled')

console.log('hand-made: keeper hold for an unrecorded transfer')
await reset(W2, { start: T0 }); const now = Date.now(); await setSync(W2, now)
await tx(W2, { t: now - 3_600_000, usdt: -8, to: USDT })
let st = (await one(`select public.treasury_status(null, now()) s`)).s
ok(st.hold && /not recorded/.test(st.hold_reason), 'USDT left in a direct transfer an hour ago and is not recorded: hold')
await db.query(`delete from public.pool_tx where wallet = $1`, [W2]); await tx(W2, { t: now - 4 * 3_600_000, usdt: -8, to: USDT })
st = (await one(`select public.treasury_status(null, now()) s`)).s
ok(!st.hold, 'the same transfer four hours ago: no hold any more (it is treated as a real outflow, as today)')
st = (await one(`select public.treasury_status(1000, now()) s`)).s
ok(st.lag_blocks !== null && st.cursor_block !== null, 'status reports how far the history is behind the chain head')
const adm = (await asRole('authenticated', ADMIN, `select public.admin_treasury_admin() a`)).rows[0].a
ok(adm.unmatched.length === 1 && near(adm.unmatched[0].amount, 8), 'the admin page can list the unrecorded transfer so it is easy to spot')

console.log('hand-made: sweep (return the excess to members)')
await reset(W2, { start: T0 }); await setSync(W2, T0 + DAY)
await db.query(`insert into public.treasury_entries (kind, amount, pot_delta, t_ms) values ('carve', 100, 100, $1)`, [T0 + 1])
for (let i = 0; i < 14; i++) await topup(W2, Date.now() - i * DAY, 0.5)
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
// the runner with a fake database
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
ok(res.kind === 'value' && res.carvePending === true, 'runner: carve could not run (history stale): value still posted, but the settlement must wait')
called = []; res = await T.treasuryValue(okDb('carved'), { due: false, forWallet: BOT, head: async () => 1000n, wallet: async () => M(590), refresh: async () => {} })
ok(!called.includes('carve'), 'runner: no carve outside the settlement run')
res = await T.treasuryValue(fake({ treasury_status: { data: act, error: null }, treasury_sync: { data: null, error: { code: 'XX000', message: 'boom' } }, treasury_carve: { data: { status: 'carved' }, error: null } }), { due: true, forWallet: BOT, head: async () => 1n, wallet: async () => M(590), refresh: async () => {} })
ok(res.kind === 'skip', 'runner: the pot cannot be brought up to date: posts nothing')

await db.end()
console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
