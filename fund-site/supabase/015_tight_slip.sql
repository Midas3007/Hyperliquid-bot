-- First-leg tolerance: the bot's first swap may slip only as far as still leaves the minimum edge (on), or a flat
-- 0.3% as before (off). Switched from Telegram (/tight_on, /tight_off). Safe to run more than once.
alter table public.bot_settings add column if not exists tight_slip boolean not null default true;
alter table public.bot_settings add column if not exists tight_updated_at timestamptz;
create or replace function public.bot_settings() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('skip_reserved', skip_reserved, 'smart_sizing', smart_sizing, 'tight_slip', tight_slip, 'updated_at', updated_at)
  from public.bot_settings where id = 1
$$;
revoke all on function public.bot_settings() from public;
grant execute on function public.bot_settings() to anon, authenticated;

-- Telegram: the last treasury withdrawal already announced
alter table public.alert_config add column if not exists last_payout_id bigint;
