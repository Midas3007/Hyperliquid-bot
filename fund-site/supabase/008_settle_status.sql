-- What the cloud bot reads to pause around the daily settlement: the day the keeper last settled (UTC).
-- Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.
create or replace function public.vault_settle_status() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('settle_day', last_settle_day, 'settle_now', settle_now, 'last_run_at', last_run_at, 'last_result', last_result)
  from public.vault_config where id = 1
$$;
revoke all on function public.vault_settle_status() from public;
grant execute on function public.vault_settle_status() to anon, authenticated;
