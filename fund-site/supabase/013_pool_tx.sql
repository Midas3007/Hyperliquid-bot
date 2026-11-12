-- Trade history kept in the database. The trade-stats function reads the pool wallet's USDT movements from a
-- Celo node, a few minutes of blocks at a time, and stores each transaction here once. The dashboard's "Trading
-- activity" and Telegram's /today are then answered from these tables, so a busy or rate-limited data source can
-- only make the numbers a few minutes old, never empty. (Before this, every refresh downloaded the whole season
-- from the block explorer, which now allows 10 requests an hour without a paid key.)
-- Only the trade-stats function (service role) reads or writes these.
-- Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.

-- One row per transaction that moved the wallet's USDT.
create table if not exists public.pool_tx (
  wallet text not null,
  hash text not null check (hash ~ '^0x[0-9a-f]{64}$'),
  block bigint not null,
  tx_index int not null,          -- position in the block: (block, tx_index) is the true order
  t bigint not null,              -- block time, milliseconds
  sent boolean not null,          -- the wallet itself sent this transaction (a trade leg, a gas top-up, a withdrawal)
  to_addr text not null default '', -- the contract or address the transaction called
  usdt numeric(30,6) not null,    -- net change of the wallet's USDT: + in, - out
  primary key (wallet, hash)
);
create index if not exists pool_tx_order on public.pool_tx (wallet, block, tx_index);

-- How far the history has been read, per wallet.
create table if not exists public.pool_sync (
  wallet text primary key,
  from_block bigint not null,     -- the history is complete from this block ...
  block bigint not null,          -- ... to this one
  block_time timestamptz,         -- when `block` was made: the numbers are "as of" this moment
  since_ms bigint not null,       -- the season start this history was read for
  synced_at timestamptz,          -- last time the reading reached the head of the chain (null: first load not finished)
  audited_at timestamptz,         -- last time the last 24 hours were re-read
  tried_at timestamptz,
  last_error text
);
alter table public.pool_tx enable row level security;
alter table public.pool_sync enable row level security;
revoke all on table public.pool_tx, public.pool_sync from anon, authenticated;

-- Store the transactions of blocks p_lo..p_hi and move the cursor to p_hi, in one transaction.
-- p_replace: the rows given are everything in that range; anything else stored there is removed (used only for
-- the most recent blocks, where a chain reorganisation can drop a transaction).
create or replace function public.pool_apply(p_wallet text, p_lo bigint, p_hi bigint, p_hi_time timestamptz, p_rows jsonb, p_replace boolean)
returns int
language plpgsql security definer set search_path = '' as $$
declare v_n int;
begin
  if p_lo is null or p_hi is null or p_lo > p_hi or p_hi_time is null or jsonb_typeof(p_rows) is distinct from 'array' then
    raise exception 'pool_apply: bad range or rows';
  end if;
  if exists (select 1 from jsonb_array_elements(p_rows) r where (r->>'block')::bigint not between p_lo and p_hi) then
    raise exception 'pool_apply: a row is outside blocks % to %', p_lo, p_hi;
  end if;
  perform 1 from public.pool_sync where wallet = p_wallet for update;
  if not found then raise exception 'pool_apply: no reading started for this wallet'; end if;

  if p_replace then
    delete from public.pool_tx x
    where x.wallet = p_wallet and x.block between p_lo and p_hi
      and not exists (select 1 from jsonb_array_elements(p_rows) r where r->>'hash' = x.hash);
  end if;

  insert into public.pool_tx (wallet, hash, block, tx_index, t, sent, to_addr, usdt)
  select p_wallet, r->>'hash', (r->>'block')::bigint, (r->>'tx_index')::int, (r->>'t')::bigint,
         (r->>'sent')::boolean, coalesce(r->>'to', ''), (r->>'usdt')::numeric
  from jsonb_array_elements(p_rows) r
  on conflict (wallet, hash) do update
    set block = excluded.block, tx_index = excluded.tx_index, t = excluded.t,
        sent = excluded.sent, to_addr = excluded.to_addr, usdt = excluded.usdt;
  get diagnostics v_n = row_count;

  -- the cursor only ever moves forward
  update public.pool_sync set block = p_hi, block_time = p_hi_time where wallet = p_wallet and block <= p_hi;
  return v_n;
end $$;

-- The trades since p_since (milliseconds), counted the way the dashboard and /today always have:
-- every transaction the wallet sent that took USDT out is a buy; the next one that brings USDT back closes the
-- trade. Profit = USDT back - USDT out. Direct USDT transfers (withdrawals) and transactions the wallet did not
-- send (deposits) are left out.
create or replace function public.pool_trades(p_wallet text, p_since bigint)
returns jsonb
language sql stable security definer set search_path = '' as $$
  with legs as (
    select t, usdt, block, tx_index,
           -- how many sales came before this leg: each sale ends a trade, so this numbers the trades
           count(*) filter (where usdt > 0)
             over (order by block, tx_index rows between unbounded preceding and 1 preceding) as trade_no
    from public.pool_tx
    where wallet = p_wallet and sent and t >= p_since and usdt <> 0
      and to_addr <> '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
  ), trades as (
    select trade_no,
           max(t) filter (where usdt > 0) as t,
           sum(-usdt) filter (where usdt < 0) as size,
           sum(usdt) filter (where usdt > 0) as back
    from legs
    group by trade_no
    -- a trade needs at least one buy and its sale (a sale with nothing bought before it is ignored;
    -- buys with no sale yet are still open)
    having count(*) filter (where usdt > 0) = 1 and count(*) filter (where usdt < 0) > 0
  )
  select coalesce(jsonb_agg(jsonb_build_object('t', t, 'size', size, 'profit', back - size) order by t, trade_no), '[]'::jsonb)
  from trades
$$;

revoke all on function public.pool_apply(text, bigint, bigint, timestamptz, jsonb, boolean) from public, anon, authenticated;
revoke all on function public.pool_trades(text, bigint) from public, anon, authenticated;
grant execute on function public.pool_apply(text, bigint, bigint, timestamptz, jsonb, boolean) to service_role;
grant execute on function public.pool_trades(text, bigint) to service_role;

-- Every 2 minutes: keep the history up to date even when nobody is looking.
do $$ begin perform cron.unschedule('pool-trades-sync'); exception when others then null; end $$;
select cron.schedule('pool-trades-sync', '*/2 * * * *', $cron$
  select net.http_post(
    url := 'https://bmudotiuafxrhzkqihyb.supabase.co/functions/v1/trade-stats',
    headers := '{"Content-Type": "application/json", "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJtdWRvdGl1YWZ4cmh6a3FpaHliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI5NjcxNDcsImV4cCI6MjEwODU0MzE0N30.xipuPyy0y78Jc3CrR5OmncD7FWT0NsPgiroorQRpnAA"}'::jsonb,
    body := '{"cron": true}'::jsonb,
    timeout_milliseconds := 60000)
$cron$);

-- The schedule keeps a log line for every run; keep a week of them.
do $$ begin perform cron.unschedule('cron-log-cleanup'); exception when others then null; end $$;
select cron.schedule('cron-log-cleanup', '41 3 * * *', $cron$
  delete from cron.job_run_details where end_time < now() - interval '7 days'
$cron$);
