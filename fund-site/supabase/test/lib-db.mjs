// Shared by the tests: a throwaway local Postgres database that looks enough like Supabase for the migrations to run
// unchanged (stub cron / net / auth schemas, the three API roles, a minimal members / settings / vault_config).
// Never touches the live project, and never touches the `postgres` database: it creates and drops its own.
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const SQL = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(import.meta.url)
export let pg
try { pg = require('pg') } catch { pg = createRequire(process.env.PG_MODULE || '/tmp/claude-0/-home-user/3a8d6223-ebc9-5af4-ae9c-0452ac68f60b/scratchpad/pg/package.json')('pg') }
export const conn = (database) => new pg.Client({ host: '127.0.0.1', port: 54329, user: 'postgres', database })
export const BOT = '0x6cd37bc290c5c02ea92086d416a6a673bca581dc'
export const VAULT = '0xd4845c6fe467845dcba2997191127d68ece4f1c4'

/** Drop and recreate database `name`, install the stubs and run the migrations in `files` (default: 009, 013, 014). */
export async function buildDb(name, files = ['009_alerts.sql', '013_pool_tx.sql', '014_treasury.sql'], { twice = false } = {}) {
  const root = conn('postgres'); await root.connect()
  await root.query(`drop database if exists ${name}`); await root.query(`create database ${name}`); await root.end()
  const db = conn(name); await db.connect()
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
    create table public.vault_config (id int primary key default 1, vault_address text);
    insert into public.vault_config values (1, '${VAULT}');
    create table public.members (user_id uuid, is_admin boolean not null default false);
    insert into public.members values ('00000000-0000-0000-0000-0000000000aa', true), ('00000000-0000-0000-0000-0000000000bb', false);
    create function public.is_admin() returns boolean language sql stable security definer set search_path = '' as $f$ select exists (select 1 from public.members where user_id = auth.uid() and is_admin) $f$;
  `)
  for (const f of files) {
    let sql = readFileSync(`${SQL}/${f}`, 'utf8')
    if (f === '009_alerts.sql') sql = sql.replace(/select net\.http_post[\s\S]*?\$cron\$\);/, 'select 1 $cron$);')
    await db.query(sql)
    if (twice && f === '014_treasury.sql') await db.query(sql)
  }
  return db
}
