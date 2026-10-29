-- Telegram alerts: the linked chat, the bot's signed heartbeat, and which alerts are active (so each one is sent
-- once, reminded every 6 hours, and followed by an "OK again" message). Only the fund-alerts function (service
-- role) reads or writes these. Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.
create table if not exists public.alert_config (
  id int primary key default 1 check (id = 1),
  chat_id bigint,
  chat_name text,
  linked_at timestamptz,
  tg_offset bigint not null default 0,
  summary_day date
);
insert into public.alert_config (id) values (1) on conflict (id) do nothing;

create table if not exists public.bot_heartbeat (
  id int primary key default 1 check (id = 1),
  at timestamptz,
  equity numeric,
  error text,
  error_streak int not null default 0,
  pause text,
  holding boolean,
  received_at timestamptz
);
insert into public.bot_heartbeat (id) values (1) on conflict (id) do nothing;

create table if not exists public.alert_state (
  key text primary key,
  active boolean not null default false,
  detail text,
  since timestamptz,
  last_sent_at timestamptz
);
alter table public.alert_config enable row level security;
alter table public.bot_heartbeat enable row level security;
alter table public.alert_state enable row level security;

-- Every 10 minutes
do $$ begin perform cron.unschedule('fund-alerts'); exception when others then null; end $$;
select cron.schedule('fund-alerts', '*/10 * * * *', $cron$
  select net.http_post(
    url := 'https://bmudotiuafxrhzkqihyb.supabase.co/functions/v1/fund-alerts',
    headers := '{"Content-Type": "application/json", "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJtdWRvdGl1YWZ4cmh6a3FpaHliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI5NjcxNDcsImV4cCI6MjEwODU0MzE0N30.xipuPyy0y78Jc3CrR5OmncD7FWT0NsPgiroorQRpnAA"}'::jsonb,
    body := '{}'::jsonb,
    timeout_milliseconds := 60000)
$cron$);
