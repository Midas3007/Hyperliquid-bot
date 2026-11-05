-- Smart sizing: the bot's max trade and minimum edge follow the time of day (kiyoshi/src/schedule.ts).
-- Switched from Telegram (/smart_on, /smart_off or the /dashboard buttons); off until switched on.
-- Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.
alter table public.bot_settings add column if not exists smart_sizing boolean not null default false;
alter table public.bot_settings add column if not exists smart_updated_at timestamptz;

create or replace function public.bot_settings() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('skip_reserved', skip_reserved, 'smart_sizing', smart_sizing, 'updated_at', updated_at)
  from public.bot_settings where id = 1
$$;
revoke all on function public.bot_settings() from public;
grant execute on function public.bot_settings() to anon, authenticated;

-- What the bot is doing now and its weekly schedule, from its heartbeat (shown by /smart and /schedule)
alter table public.bot_heartbeat add column if not exists smart_summary text;
alter table public.bot_heartbeat add column if not exists smart_schedule text;
