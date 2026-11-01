-- New-corridor watch: the currencies already reported (one Telegram message each), and a daily run at 09:23 UTC.
-- Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.
create table if not exists public.corridor_seen (
  code text primary key,
  detail jsonb,
  seen_at timestamptz not null default now()
);
alter table public.corridor_seen enable row level security;

do $$ begin perform cron.unschedule('corridor-watch'); exception when others then null; end $$;
select cron.schedule('corridor-watch', '23 9 * * *', $cron$
  select net.http_post(
    url := 'https://bmudotiuafxrhzkqihyb.supabase.co/functions/v1/corridor-watch',
    headers := '{"Content-Type": "application/json", "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJtdWRvdGl1YWZ4cmh6a3FpaHliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI5NjcxNDcsImV4cCI6MjEwODU0MzE0N30.xipuPyy0y78Jc3CrR5OmncD7FWT0NsPgiroorQRpnAA"}'::jsonb,
    body := '{}'::jsonb,
    timeout_milliseconds := 60000)
$cron$);
