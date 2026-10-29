-- On-chain fund support: vault settings, the keeper's history and wallet key, the hourly keeper job, and the
-- public overview the wallet-based site reads. Paste into Supabase, SQL Editor, New query, Run.
-- Safe to run more than once.

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Which vault the site and the keeper use (set from the Admin page after a deploy).
create table if not exists public.vault_config (
  id int primary key default 1 check (id = 1),
  network text not null default 'testnet' check (network in ('testnet', 'mainnet')),
  vault_address text check (vault_address is null or vault_address ~ '^0x[0-9a-fA-F]{40}$'),
  usdt_address text check (usdt_address is null or usdt_address ~ '^0x[0-9a-fA-F]{40}$'),
  keeper_address text,
  last_run_at timestamptz,
  last_settle_day date,
  last_result text,
  updated_at timestamptz not null default now()
);
insert into public.vault_config (id, network, vault_address, usdt_address)
values (1, 'testnet', '0xd91374cb5ae00c2365d4b2680b12b8db19a7db67', '0x3c69e9a55eb1ccea9f8c3ed2c94db355d3a1018d')
on conflict (id) do nothing;

-- Every value report and settlement the keeper sends (the share price chart).
create table if not exists public.vault_history (
  id bigserial primary key,
  network text not null,
  vault_address text not null,
  kind text not null check (kind in ('report', 'settle')),
  nav numeric not null,
  supply numeric not null,
  price numeric not null,
  tx text,
  at timestamptz not null default now()
);
create index if not exists vault_history_at on public.vault_history (vault_address, at);
alter table public.vault_config enable row level security;
alter table public.vault_history enable row level security;

-- The keeper wallet's key lives encrypted in Supabase Vault; only the keeper function (service role) reads it.
create or replace function public._keeper_key() returns text
language sql stable security definer set search_path = '' as $$
  select decrypted_secret from vault.decrypted_secrets where name = 'fund_vault_keeper_key' limit 1
$$;
create or replace function public._keeper_set_key(p_key text, p_address text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if exists (select 1 from vault.secrets where name = 'fund_vault_keeper_key') then raise exception 'The keeper key already exists'; end if;
  perform vault.create_secret(p_key, 'fund_vault_keeper_key', 'Fund vault keeper (operator) wallet');
  update public.vault_config set keeper_address = p_address, updated_at = now() where id = 1;
end $$;
revoke all on function public._keeper_key(), public._keeper_set_key(text, text) from public, anon, authenticated;
grant execute on function public._keeper_key(), public._keeper_set_key(text, text) to service_role;

-- Admin: point the site and the keeper at a vault (after `npm run deploy`).
create or replace function public.admin_set_vault(p_network text, p_vault text, p_usdt text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  update public.vault_config set network = p_network, vault_address = nullif(trim(p_vault), ''), usdt_address = nullif(trim(p_usdt), ''),
    last_settle_day = null, updated_at = now() where id = 1;
end $$;
revoke all on function public.admin_set_vault(text, text, text) from public, anon;
grant execute on function public.admin_set_vault(text, text, text) to authenticated;

-- Public overview: everything the wallet-based site shows (no names or per-member amounts).
create or replace function public.public_overview() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare s public.settings; v public.vault_config;
begin
  select * into s from public.settings where id = 1;
  select * into v from public.vault_config where id = 1;
  return jsonb_build_object(
    'fund_name', s.fund_name, 'fee_pct', s.fee_pct, 'total_slots', s.total_slots, 'bot_wallet', s.bot_wallet,
    'members', (select count(*) from (select member_id from public.ledger group by member_id having sum(units) > 0) a),
    'season', s.season, 'season_started_at', s.season_started_at,
    'current_value', coalesce(public.current_value(), 0), 'unit_price', public.unit_price(),
    'last_updated', (select max(recorded_at) from public.nav_snapshots),
    'history', public._price_history(),
    'leaderboard', public._board(public._leaderboard(), null),
    'seasons', public._seasons_for(null),
    'vault', jsonb_build_object('network', v.network, 'address', v.vault_address, 'usdt', v.usdt_address,
      'keeper', v.keeper_address, 'last_run_at', v.last_run_at, 'last_result', v.last_result),
    'vault_history', (select coalesce(jsonb_agg(jsonb_build_object('t', at, 'value', nav, 'unit_price', price, 'kind', kind) order by at), '[]'::jsonb)
      from (select * from public.vault_history where vault_address = v.vault_address order by at desc limit 2000) h)
  );
end $$;
revoke all on function public.public_overview() from public;
grant execute on function public.public_overview() to anon, authenticated;

-- Run the keeper every hour, at 7 minutes past.
do $$ begin perform cron.unschedule('fund-vault-keeper'); exception when others then null; end $$;
select cron.schedule('fund-vault-keeper', '7 * * * *', $cron$
  select net.http_post(
    url := 'https://bmudotiuafxrhzkqihyb.supabase.co/functions/v1/vault-keeper',
    headers := '{"Content-Type": "application/json", "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJtdWRvdGl1YWZ4cmh6a3FpaHliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI5NjcxNDcsImV4cCI6MjEwODU0MzE0N30.xipuPyy0y78Jc3CrR5OmncD7FWT0NsPgiroorQRpnAA"}'::jsonb,
    body := '{}'::jsonb,
    timeout_milliseconds := 60000)
$cron$);

-- One-time early settlement (the keeper clears it after use).
alter table public.vault_config add column if not exists settle_now boolean not null default false;
