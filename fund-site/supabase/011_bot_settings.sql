-- Bot settings switched from Telegram (fund-alerts, service role) and read by the bot through a public call.
-- skip_reserved: skip a Textile -> Uniswap trade when Textile still reserves funds, instead of waiting two minutes.
-- Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.
create table if not exists public.bot_settings (
  id int primary key default 1 check (id = 1),
  skip_reserved boolean not null default true,
  updated_at timestamptz not null default now()
);
insert into public.bot_settings (id) values (1) on conflict (id) do nothing;
alter table public.bot_settings enable row level security;

create or replace function public.bot_settings() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('skip_reserved', skip_reserved, 'updated_at', updated_at) from public.bot_settings where id = 1
$$;
revoke all on function public.bot_settings() from public;
grant execute on function public.bot_settings() to anon, authenticated;

-- The bot's skip count, from its heartbeat (shown by /skipwait)
alter table public.bot_heartbeat add column if not exists skip_summary text;
