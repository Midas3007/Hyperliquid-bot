-- Fund treasury: one pot that pays for the fund's running costs (gas and Render hosting).
-- Decided by Midas on 12 Nov: 10% of the pool's trading profit goes into the pot; the pot pays for the bot's gas
-- top-ups and for Render; members see it openly; Midas's own fees are untouched.
--
-- How it works, in plain words (the full write-up is kiyoshi/docs/treasury-design.md):
--  * The pot is NOT a separate wallet. It is a line in this database over USDT that stays in the bot wallet. The
--    keeper posts "what the wallet is worth MINUS the pot", so money in the pot is not part of members' share
--    value. Anything the pot pays for (gas, Render) leaves the wallet and the pot by the same amount, so the
--    difference, which is the members' value, does not move.
--  * Once per settlement day the keeper "carves" 10% of that window's realised trading profit into the pot
--    (treasury_carve). Gas top-ups are read back from pool_tx (the wallet's own transactions) and drawn from the
--    pot (treasury_sync). Render withdrawals are recorded by Midas on the admin page and matched to the real USDT
--    transfer when it shows up in pool_tx. Nothing here moves money; the pot only records what happened.
--  * If the pot cannot cover a gas top-up or a withdrawal, it covers what it can and the rest falls on members,
--    exactly as gas does today. The log shows that part as "paid by members".
--  * Nothing happens until Midas presses "Start the treasury" on the admin page (admin_treasury_start): the pot
--    starts at zero and only counts what happens after that moment.
--
-- Tables are for the keeper and the admin functions only; members read treasury_overview() and nothing else.
-- Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.
-- Needs 009 (bot_heartbeat) and 013 (pool_tx, pool_sync).

-- Settings. One row. Change them with admin_treasury_settings(), never by hand.
create table if not exists public.treasury_config (
  id int primary key default 1 check (id = 1),
  active boolean not null default false,        -- off until Midas starts it; off means the keeper behaves as before
  wallet text check (wallet is null or wallet ~ '^0x[0-9a-f]{40}$'), -- the pool wallet the pot is a line over (lower case)
  started_ms bigint,                            -- only trades, top-ups and withdrawals after this moment count
  carve_pct numeric(6,4) not null default 0.10 check (carve_pct between 0 and 0.5), -- share of profit that goes in
  carry_losses boolean not null default true,   -- a losing stretch must be won back before the next carve (see treasury_carve)
  router text not null default '0x5615cdab10dc425a742d643d949a7f474c01abc4' check (router ~ '^0x[0-9a-f]{40}$'), -- the Uniswap router the bot buys CELO through
  gas_max_usd numeric(10,6) not null default 1 check (gas_max_usd > 0), -- a router call of at most this much USDT is a gas top-up (the bot spends $0.50)
  render_usd_per_month numeric(10,2) not null default 8 check (render_usd_per_month >= 0), -- only used for "days of costs covered"
  target_days int not null default 60 check (target_days > 0), -- the pot is "full" at this many days of costs; more is "excess"
  hold_minutes int not null default 180,        -- how long the keeper waits for a Render withdrawal to be matched
  updated_at timestamptz not null default now()
);
insert into public.treasury_config (id) values (1) on conflict (id) do nothing;

-- One row per settlement day the carve has run, even when nothing was carved (so it can never run twice for a day,
-- and the windows follow each other with no gap and no overlap).
create table if not exists public.treasury_days (
  day date primary key,                 -- the settlement day (UTC)
  win_from bigint not null,             -- trades that closed after this moment (ms) ...
  win_to bigint not null,               -- ... up to and including this one were counted
  trades int not null,
  profit numeric(18,6) not null,        -- realised profit of those trades, gas top-ups left out
  carry_in numeric(18,6) not null,      -- losses still to be won back before this day (0 or negative)
  carry_out numeric(18,6) not null,
  carve numeric(18,6) not null check (carve >= 0),
  created_at timestamptz not null default now()
);

-- Every movement of the pot. The balance is the sum of pot_delta.
create table if not exists public.treasury_entries (
  id bigserial primary key,
  kind text not null check (kind in ('carve', 'gas', 'render', 'sweep')),
  status text not null default 'done' check (status in ('done', 'pending', 'void')), -- pending/void: Render only
  amount numeric(18,6) not null check (amount > 0),   -- the size of the movement in USDT
  pot_delta numeric(18,6) not null default 0,         -- what it did to the pot: carve +, the rest - (only what the pot could cover)
  uncovered numeric(18,6) not null default 0,         -- what the pot could not cover: paid by members' value
  t_ms bigint,                                        -- when it happened (chain time; the carve: the end of its window)
  day date,                                           -- carve: the settlement day
  tx text check (tx is null or tx ~ '^0x[0-9a-f]{64}$'), -- gas top-up or Render transfer on the chain
  paid_on date,                                       -- Render: the date Midas entered
  celo numeric(30,9),                                 -- gas: CELO bought (from the bot's heartbeat, when known)
  note text check (note is null or char_length(note) <= 200),
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint treasury_entries_shape check (
    (status = 'done' and kind = 'carve' and pot_delta = amount and uncovered = 0)
    or (status = 'done' and kind <> 'carve' and uncovered >= 0 and uncovered <= amount and pot_delta = -(amount - uncovered))
    or (status in ('pending', 'void') and kind = 'render' and pot_delta = 0 and uncovered = 0)
  )
);
-- the same transaction can only ever be recorded once, and a day can only be carved once
create unique index if not exists treasury_entries_gas_tx on public.treasury_entries (tx) where kind = 'gas';
create unique index if not exists treasury_entries_render_tx on public.treasury_entries (tx) where kind = 'render' and tx is not null and status = 'done';
create unique index if not exists treasury_entries_carve_day on public.treasury_entries (day) where kind = 'carve';
create index if not exists treasury_entries_order on public.treasury_entries (id);

alter table public.treasury_config enable row level security;
alter table public.treasury_days enable row level security;
alter table public.treasury_entries enable row level security;
revoke all on table public.treasury_config, public.treasury_days, public.treasury_entries from public, anon, authenticated;
revoke all on sequence public.treasury_entries_id_seq from public, anon, authenticated;

-- The pot's balance right now.
create or replace function public._treasury_balance() returns numeric
language sql stable security definer set search_path = '' as $$
  select coalesce(sum(pot_delta), 0) from public.treasury_entries where status = 'done'
$$;

-- Realised profit of the trades that CLOSED after p_from and up to p_to (milliseconds), counted like the dashboard's
-- pool_trades (013), with one difference: a gas top-up is not part of any trade. The dashboard's "profit before
-- gas" treats each top-up (USDT -> CELO through the router) as money out of the next trade, so its number is
-- already net of gas. The pot pays gas, so profit here is counted before it; otherwise members would pay
-- for gas twice (once in the profit that gets carved, once from the pot).
-- A trade belongs to the window its sale closed in, so every trade is counted in exactly one window.
create or replace function public._treasury_profit(p_wallet text, p_from bigint, p_to bigint) returns jsonb
language sql stable security definer set search_path = '' as $$
  with cfg as (select lower(router) as router, gas_max_usd from public.treasury_config where id = 1),
  sy as (select since_ms from public.pool_sync where wallet = p_wallet),
  legs as (
    select x.t, x.usdt,
           count(*) filter (where x.usdt > 0) over (order by x.block, x.tx_index rows between unbounded preceding and 1 preceding) as trade_no,
           (x.to_addr = cfg.router and x.usdt < 0 and x.usdt >= -cfg.gas_max_usd) as is_topup
    from public.pool_tx x, cfg, sy
    where x.wallet = p_wallet and x.sent and x.t >= sy.since_ms and x.usdt <> 0
      and x.to_addr <> '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
  ), trades as (
    select max(t) filter (where usdt > 0) as t,
           sum(-usdt) filter (where usdt < 0 and not is_topup) as size,
           sum(usdt) filter (where usdt > 0) as back
    from legs
    group by trade_no
    having count(*) filter (where usdt > 0) = 1 and count(*) filter (where usdt < 0 and not is_topup) > 0
  )
  select jsonb_build_object('trades', count(*), 'profit', coalesce(sum(back - size), 0))
  from trades where t > p_from and t <= p_to
$$;

-- Keeper (service role) and a 5-minute schedule: bring the pot up to date with what the wallet has done.
--  1. Gas: every top-up in pool_tx (sent by the wallet to the router, a small amount of USDT out) is drawn from the
--     pot once. The transaction hash is unique in treasury_entries, so seeing the same top-up twice changes nothing.
--     If the pot is short, it pays what it holds and the rest is recorded as paid by members.
--  2. Render: a withdrawal Midas recorded is matched to the direct USDT transfer out of the wallet (same amount, sent
--     by the wallet, not already matched) and only then leaves the pot.
-- Only transactions up to the history's own "as of" time are read, so the pot never runs ahead of pool_tx.
create or replace function public.treasury_sync() returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  c public.treasury_config; cur bigint; r record; m record;
  v_bal numeric; v_cover numeric; v_gas int := 0; v_render int := 0; v_n int;
begin
  select * into c from public.treasury_config where id = 1 for update;
  if not c.active then return jsonb_build_object('active', false); end if;
  select floor(extract(epoch from block_time) * 1000)::bigint into cur from public.pool_sync where wallet = c.wallet;
  if cur is null then return jsonb_build_object('active', true, 'waiting', 'the trade history is not loaded yet'); end if;

  for r in
    select x.hash, x.t, -x.usdt as amount
    from public.pool_tx x
    where x.wallet = c.wallet and x.sent and x.to_addr = lower(c.router) and x.usdt < 0 and x.usdt >= -c.gas_max_usd
      and x.t > c.started_ms and x.t <= cur
      and not exists (select 1 from public.treasury_entries e where e.kind = 'gas' and e.tx = x.hash)
    order by x.block, x.tx_index
  loop
    v_bal := public._treasury_balance();
    v_cover := least(r.amount, v_bal);
    insert into public.treasury_entries (kind, amount, pot_delta, uncovered, t_ms, tx, note)
    values ('gas', r.amount, -v_cover, r.amount - v_cover, r.t, r.hash,
            case when v_cover < r.amount then 'The pot was short: the rest was paid by members' else null end)
    on conflict (tx) where kind = 'gas' do nothing;
    get diagnostics v_n = row_count;
    v_gas := v_gas + v_n;
  end loop;

  -- the CELO each top-up bought, from the bot's heartbeat (it only ever carries the last one)
  begin
    update public.treasury_entries e set celo = (h.last_topup ->> 'celo')::numeric
    from public.bot_heartbeat h
    where h.id = 1 and e.kind = 'gas' and e.celo is null and e.tx = lower(h.last_topup ->> 'tx')
      and (h.last_topup ->> 'celo') ~ '^[0-9]+(\.[0-9]+)?$';
  exception when others then null; -- cosmetic only
  end;

  for r in select * from public.treasury_entries where kind = 'render' and status = 'pending' order by id loop
    select x.hash, x.t into m
    from public.pool_tx x
    where x.wallet = c.wallet and x.sent and x.to_addr = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
      and x.usdt < 0 and abs(x.usdt + r.amount) < 0.005
      and x.t > c.started_ms and x.t <= cur
      and x.t >= (extract(epoch from r.created_at) * 1000)::bigint - 7 * 86400000 -- sent up to a week before it was recorded
      and (r.tx is null or x.hash = r.tx)
      and not exists (select 1 from public.treasury_entries e where e.kind = 'render' and e.status = 'done' and e.tx = x.hash)
    order by x.block, x.tx_index
    limit 1;
    if found then
      v_bal := public._treasury_balance();
      v_cover := least(r.amount, v_bal);
      update public.treasury_entries
        set status = 'done', tx = m.hash, t_ms = m.t, pot_delta = -v_cover, uncovered = r.amount - v_cover,
            note = case when v_cover < r.amount then coalesce(note || ' · ', '') || 'The pot was short: the rest was paid by members' else note end
        where id = r.id;
      v_render := v_render + 1;
    end if;
  end loop;
  return jsonb_build_object('active', true, 'gas_recorded', v_gas, 'render_matched', v_render, 'as_of_ms', cur);
end $$;

-- Keeper (service role): the daily carve, run in the same keeper run as the settlement and BEFORE the value is posted.
-- Carve = carve_pct (10%) of the realised trading profit of the window since the previous carve, gas top-ups left out.
-- One carve per settlement day (the day is the key: a second run the same day returns "already" and changes
-- nothing). Windows follow each other: each one starts where the last ended, and ends at the moment the trade history
-- has been read up to, so a trade is never missed and never counted twice. If the history is more than 15 minutes
-- old nothing is carved ("stale"); the keeper tries again an hour later.
-- A losing day carves nothing. With carry_losses on (the default) the loss is remembered: later profit first wins it
-- back, and only profit above the previous high point is carved. In other words the pot takes 10% of NET profit, so
-- members never pay 10% on gains that only made up an earlier loss. With it off, every winning day is carved on its
-- own and losing days are simply skipped.
create or replace function public.treasury_carve(p_now timestamptz default now()) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  c public.treasury_config; v_day date := (p_now at time zone 'utc')::date; d public.treasury_days; sy record;
  v_from bigint; v_to bigint; v_carry_in numeric := 0; v_prof jsonb; v_profit numeric; v_base numeric; v_carve numeric; v_carry_out numeric;
begin
  select * into c from public.treasury_config where id = 1 for update;
  if not c.active then return jsonb_build_object('status', 'inactive'); end if;
  select * into d from public.treasury_days where day = v_day;
  if found then return jsonb_build_object('status', 'already', 'day', v_day, 'carve', d.carve); end if;
  select * into sy from public.pool_sync where wallet = c.wallet;
  if not found or sy.synced_at is null or sy.block_time is null or p_now - sy.block_time > interval '15 minutes' then
    return jsonb_build_object('status', 'stale', 'as_of', sy.block_time);
  end if;
  v_to := floor(extract(epoch from sy.block_time) * 1000)::bigint;
  select win_to, carry_out into v_from, v_carry_in from public.treasury_days order by day desc limit 1;
  if not found then v_from := c.started_ms; v_carry_in := 0; end if;
  if v_to <= v_from then return jsonb_build_object('status', 'stale', 'as_of', sy.block_time); end if;

  v_prof := public._treasury_profit(c.wallet, v_from, v_to);
  v_profit := (v_prof ->> 'profit')::numeric;
  if c.carry_losses then
    v_base := v_profit + v_carry_in;                -- v_carry_in is 0 or negative
    v_carve := case when v_base > 0 then round(v_base * c.carve_pct, 6) else 0 end;
    v_carry_out := least(v_base, 0);
  else
    v_carve := case when v_profit > 0 then round(v_profit * c.carve_pct, 6) else 0 end;
    v_carry_out := 0;
  end if;
  insert into public.treasury_days (day, win_from, win_to, trades, profit, carry_in, carry_out, carve)
  values (v_day, v_from, v_to, (v_prof ->> 'trades')::int, v_profit, v_carry_in, v_carry_out, v_carve);
  if v_carve > 0 then
    insert into public.treasury_entries (kind, amount, pot_delta, t_ms, day, detail)
    values ('carve', v_carve, v_carve, v_to, v_day,
            jsonb_build_object('win_from', v_from, 'win_to', v_to, 'profit', v_profit, 'carry_in', v_carry_in, 'pct', c.carve_pct));
  end if;
  return jsonb_build_object('status', 'carved', 'day', v_day, 'profit', v_profit, 'carry_in', v_carry_in, 'carve', v_carve);
end $$;

-- Keeper (service role): what it needs before it posts a value.
--  balance: the pot, to subtract from the wallet's value.
--  hold: true while a Render withdrawal is waiting to be matched (recorded but not seen on the chain yet, or seen on
--        the chain but not recorded yet), for hold_minutes. The wallet's value and the pot would disagree for a
--        few minutes, so the keeper posts nothing and tries again at its next run.
--  lag_blocks: how far the stored history is behind the chain head p_head_block (null if not given).
create or replace function public.treasury_status(p_head_block bigint default null, p_now timestamptz default now()) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare c public.treasury_config; sy record; v_since bigint; v_reason text;
begin
  select * into c from public.treasury_config where id = 1;
  if not c.active then return jsonb_build_object('active', false); end if;
  select * into sy from public.pool_sync where wallet = c.wallet;
  v_since := floor(extract(epoch from p_now) * 1000)::bigint - c.hold_minutes * 60000;
  if exists (select 1 from public.treasury_entries e where e.kind = 'render' and e.status = 'pending'
             and e.created_at > p_now - make_interval(mins => c.hold_minutes)) then
    v_reason := 'a Render withdrawal was recorded and is not matched to the chain yet';
  elsif exists (select 1 from public.pool_tx x
                where x.wallet = c.wallet and x.sent and x.to_addr = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e' and x.usdt < 0
                  and x.t > greatest(c.started_ms, v_since)
                  and not exists (select 1 from public.treasury_entries e where e.kind = 'render' and e.status = 'done' and e.tx = x.hash)) then
    v_reason := 'USDT left the wallet in a direct transfer that is not recorded as a Render withdrawal yet';
  end if;
  return jsonb_build_object('active', true, 'wallet', c.wallet, 'balance', public._treasury_balance(),
    'hold', v_reason is not null, 'hold_reason', v_reason,
    'cursor_block', sy.block, 'cursor_time', sy.block_time,
    'lag_blocks', case when p_head_block is null or sy.block is null then null else p_head_block - sy.block end);
end $$;

-- Gas per day over the last 14 days, from the wallet's own top-ups (whether or not the treasury had started).
create or replace function public._treasury_gas_per_day(p_now timestamptz default now()) returns numeric
language sql stable security definer set search_path = '' as $$
  with c as (select * from public.treasury_config where id = 1),
  now_ms as (select floor(extract(epoch from p_now) * 1000)::bigint as v),
  span as (select greatest(1, least(14, ((select v from now_ms) - min(x.t)) / 86400000.0)) as days
           from public.pool_tx x, c where x.wallet = c.wallet),
  spent as (select coalesce(sum(-x.usdt), 0) as usdt from public.pool_tx x, c
            where x.wallet = c.wallet and x.sent and x.to_addr = lower(c.router) and x.usdt < 0 and x.usdt >= -c.gas_max_usd
              and x.t > (select v from now_ms) - 14 * 86400000)
  select coalesce((select usdt from spent) / nullif((select days from span), 0), 0)
$$;

-- What members may see (public, like public_overview): the pot, this month's movements, how long it lasts,
-- and the log. No addresses, no names, nothing about pending admin work.
create or replace function public.treasury_overview() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  c public.treasury_config; v_bal numeric; v_gas_day numeric; v_cost_day numeric; v_target numeric; v_committed numeric;
  v_month_ms bigint; v_now_ms bigint := floor(extract(epoch from now()) * 1000)::bigint; v_last public.treasury_days;
begin
  select * into c from public.treasury_config where id = 1;
  if not c.active then return jsonb_build_object('active', false); end if;
  v_bal := public._treasury_balance();
  v_gas_day := public._treasury_gas_per_day();
  v_cost_day := v_gas_day + c.render_usd_per_month * 12 / 365;
  v_target := v_cost_day * c.target_days;
  select coalesce(sum(amount), 0) into v_committed from public.treasury_entries where kind = 'render' and status = 'pending';
  v_month_ms := floor(extract(epoch from (date_trunc('month', now() at time zone 'utc') at time zone 'utc')) * 1000)::bigint;
  select * into v_last from public.treasury_days order by day desc limit 1;
  return jsonb_build_object(
    'active', true, 'started_at', to_timestamp(c.started_ms / 1000.0), 'carve_pct', c.carve_pct,
    'balance', v_bal, 'target_days', c.target_days, 'target_balance', round(v_target, 2),
    'level', case when v_target > 0 then least(1, v_bal / v_target) else null end,
    'gas_per_day', round(v_gas_day, 4), 'render_per_day', round(c.render_usd_per_month * 12 / 365, 4),
    'cost_per_day', round(v_cost_day, 4),
    'days_covered', case when v_cost_day > 0 then round(v_bal / v_cost_day, 1) else null end,
    'excess', round(greatest(0, v_bal - v_committed - v_target), 2),
    'month', (select jsonb_build_object('label', to_char(now() at time zone 'utc', 'FMMonth YYYY'),
        'carved', coalesce(sum(amount) filter (where kind = 'carve'), 0),
        'gas', coalesce(sum(amount) filter (where kind = 'gas'), 0),
        'render', coalesce(sum(amount) filter (where kind = 'render'), 0),
        'sweep', coalesce(sum(amount) filter (where kind = 'sweep'), 0))
      from public.treasury_entries where status = 'done' and t_ms >= v_month_ms),
    'total', (select jsonb_build_object('carved', coalesce(sum(amount) filter (where kind = 'carve'), 0),
        'gas', coalesce(sum(amount) filter (where kind = 'gas'), 0), 'render', coalesce(sum(amount) filter (where kind = 'render'), 0),
        'sweep', coalesce(sum(amount) filter (where kind = 'sweep'), 0), 'paid_by_members', coalesce(sum(uncovered), 0))
      from public.treasury_entries where status = 'done'),
    'last_carve', case when v_last.day is null then null else jsonb_build_object('day', v_last.day, 'profit', v_last.profit, 'carve', v_last.carve,
        'to_ms', v_last.win_to, 'losses_to_win_back', -v_last.carry_out) end,
    'pending_render', (select count(*) from public.treasury_entries where kind = 'render' and status = 'pending'),
    'log', (select coalesce(jsonb_agg(l order by (l ->> 't')::bigint desc, (l ->> 'sort')::bigint desc), '[]'::jsonb) from (
        select l from (
          select jsonb_build_object('sort', e.id, 'kind', e.kind, 'amount', e.amount, 'from_pot', abs(e.pot_delta),
                 'uncovered', e.uncovered, 't', coalesce(e.t_ms, floor(extract(epoch from e.created_at) * 1000)::bigint),
                 'tx', e.tx, 'note', e.note, 'celo', e.celo, 'pending', e.status = 'pending',
                 'paid_on', e.paid_on, 'profit', (e.detail ->> 'profit')::numeric) as l
          from public.treasury_entries e where e.status in ('done', 'pending')
          union all
          -- days that carved nothing (a losing or empty day) are logged too, so the rule is visible
          select jsonb_build_object('sort', 0, 'kind', 'carve', 'amount', 0, 'from_pot', 0, 'uncovered', 0, 't', z.win_to, 'tx', null,
                 'note', case when z.carry_out < 0 then 'Nothing carved: a loss of $' || to_char(-z.carry_out, 'FM999990.00') || ' still has to be won back first'
                              else 'Nothing carved: no profit in this window' end,
                 'celo', null, 'pending', false, 'paid_on', null, 'profit', z.profit)
          from public.treasury_days z where z.carve = 0
        ) u order by (l ->> 't')::bigint desc, (l ->> 'sort')::bigint desc limit 100) x)
  );
end $$;

-- ---------- admin (the signed-in admin only; none of these moves money) ----------

-- Switch the treasury on. The pot starts at zero now; only what happens after this moment is counted.
-- Nothing is back-dated (carving the earlier season would take money out of members' value at once and show as a loss).
create or replace function public.admin_treasury_start() returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c public.treasury_config; v_wallet text;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into c from public.treasury_config where id = 1 for update;
  if c.active then raise exception 'The treasury is already running'; end if;
  select lower(bot_wallet) into v_wallet from public.settings where id = 1;
  if v_wallet is null or v_wallet !~ '^0x[0-9a-f]{40}$' then raise exception 'Set the pool wallet in Settings first'; end if;
  if not exists (select 1 from public.pool_sync where wallet = v_wallet and synced_at is not null) then
    raise exception 'The trade history has not been loaded yet; try again in a few minutes';
  end if;
  update public.treasury_config set active = true, wallet = v_wallet, started_ms = floor(extract(epoch from now()) * 1000)::bigint, updated_at = now() where id = 1;
  return jsonb_build_object('started', true);
end $$;

-- Change the settings (they apply from the next carve). Any argument left out stays as it is.
create or replace function public.admin_treasury_settings(p_carve_pct numeric default null, p_carry_losses boolean default null,
  p_render_usd_per_month numeric default null, p_target_days int default null) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  update public.treasury_config set carve_pct = coalesce(p_carve_pct, carve_pct), carry_losses = coalesce(p_carry_losses, carry_losses),
    render_usd_per_month = coalesce(p_render_usd_per_month, render_usd_per_month), target_days = coalesce(p_target_days, target_days),
    updated_at = now() where id = 1;
end $$;

-- Record a Render withdrawal Midas has made (or is about to make) from his phone. It leaves the pot when the matching
-- USDT transfer shows up on the chain (usually within minutes). More than the pot can cover is refused unless
-- p_allow_over is true; then the pot pays what it holds and the rest comes out of members' value, and the log says so.
create or replace function public.admin_treasury_record_render(p_amount numeric, p_paid_on date, p_note text default null,
  p_tx text default null, p_allow_over boolean default false) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c public.treasury_config; v_free numeric; v_id bigint; v_tx text := lower(nullif(trim(p_tx), '')); v_amount numeric := round(p_amount, 2);
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into c from public.treasury_config where id = 1 for update;
  if not c.active then raise exception 'Start the treasury first'; end if;
  if v_amount is null or v_amount <= 0 or v_amount > 10000 then raise exception 'Enter the amount in USDT (more than 0)'; end if;
  if p_paid_on is null or p_paid_on > (now() at time zone 'utc')::date + 1 or p_paid_on < (now() at time zone 'utc')::date - 60 then
    raise exception 'Check the date';
  end if;
  if char_length(coalesce(p_note, '')) > 200 then raise exception 'The note is too long (200 characters at most)'; end if;
  if v_tx is not null and v_tx !~ '^0x[0-9a-f]{64}$' then raise exception 'The transaction hash is 0x followed by 64 characters'; end if;
  v_free := public._treasury_balance() - coalesce((select sum(amount) from public.treasury_entries where kind = 'render' and status = 'pending'), 0);
  if v_amount > v_free and not coalesce(p_allow_over, false) then
    raise exception 'The treasury holds only $% free of other waiting withdrawals. The rest ($%) would come out of members'' value. Record it anyway only if the transfer is already made or you accept that.',
      to_char(greatest(v_free, 0), 'FM999990.00'), to_char(v_amount - greatest(v_free, 0), 'FM999990.00');
  end if;
  insert into public.treasury_entries (kind, status, amount, paid_on, tx, note)
  values ('render', 'pending', v_amount, p_paid_on, v_tx, coalesce(nullif(trim(p_note), ''), 'Render hosting')) returning id into v_id;
  perform public.treasury_sync(); -- match it right away if the transfer is already in the history
  return jsonb_build_object('id', v_id, 'matched', (select status = 'done' from public.treasury_entries where id = v_id));
end $$;

-- Cancel a recorded withdrawal that has not been matched (a typo, or the transfer was never made).
create or replace function public.admin_treasury_cancel_render(p_id bigint) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  update public.treasury_entries set status = 'void' where id = p_id and kind = 'render' and status = 'pending';
  if not found then raise exception 'That withdrawal is not waiting any more'; end if;
end $$;

-- The plan's sweep rule, as a manual, logged action: when the pot holds more than target_days of costs, release the
-- excess to members. The pot goes down and members' value goes up by the same amount at the next value report
-- (a small step up, shown in the log as "returned to members"). Not automatic: it changes members' value, so Midas decides when.
create or replace function public.admin_treasury_sweep(p_amount numeric default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c public.treasury_config; v_free numeric; v_excess numeric; v_amount numeric; v_target numeric;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into c from public.treasury_config where id = 1 for update;
  if not c.active then raise exception 'Start the treasury first'; end if;
  v_free := public._treasury_balance() - coalesce((select sum(amount) from public.treasury_entries where kind = 'render' and status = 'pending'), 0);
  v_target := (public._treasury_gas_per_day() + c.render_usd_per_month * 12 / 365) * c.target_days;
  v_excess := greatest(0, round(v_free - v_target, 2));
  v_amount := round(coalesce(p_amount, v_excess), 2);
  if v_amount <= 0 or v_amount > v_excess then
    raise exception 'The most that can be returned right now is $% (everything above % days of costs)', to_char(v_excess, 'FM999990.00'), c.target_days;
  end if;
  insert into public.treasury_entries (kind, amount, pot_delta, t_ms, note)
  values ('sweep', v_amount, -v_amount, floor(extract(epoch from now()) * 1000)::bigint, 'Returned to members: more than ' || c.target_days || ' days of costs');
  return jsonb_build_object('returned', v_amount);
end $$;

-- What the admin page needs: the settings, withdrawals waiting to be matched, and direct transfers out of the wallet
-- that are not recorded as Render (so a forgotten one is easy to spot).
create or replace function public.admin_treasury_admin() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare c public.treasury_config;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into c from public.treasury_config where id = 1;
  return jsonb_build_object(
    'active', c.active, 'carry_losses', c.carry_losses, 'carve_pct', c.carve_pct, 'target_days', c.target_days,
    'balance', public._treasury_balance(),
    'committed', coalesce((select sum(amount) from public.treasury_entries where kind = 'render' and status = 'pending'), 0),
    'pending', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'amount', amount, 'paid_on', paid_on, 'note', note, 'tx', tx) order by id), '[]'::jsonb)
                from public.treasury_entries where kind = 'render' and status = 'pending'),
    'unmatched', case when not c.active then '[]'::jsonb else (select coalesce(jsonb_agg(jsonb_build_object('hash', x.hash, 't', x.t, 'amount', -x.usdt) order by x.t desc), '[]'::jsonb)
                from (select * from public.pool_tx p where p.wallet = c.wallet and p.sent and p.to_addr = '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e'
                        and p.usdt < 0 and p.t > c.started_ms
                        and not exists (select 1 from public.treasury_entries e where e.kind = 'render' and e.status = 'done' and e.tx = p.hash)
                      order by p.t desc limit 10) x) end
  );
end $$;

-- Privileges. The keeper's functions: service role only. Members: the overview only. Admin functions check is_admin().
revoke all on function public._treasury_balance(), public._treasury_gas_per_day(timestamptz), public._treasury_profit(text, bigint, bigint),
  public.treasury_sync(), public.treasury_carve(timestamptz), public.treasury_status(bigint, timestamptz), public.treasury_overview(),
  public.admin_treasury_start(), public.admin_treasury_settings(numeric, boolean, numeric, int),
  public.admin_treasury_record_render(numeric, date, text, text, boolean), public.admin_treasury_cancel_render(bigint),
  public.admin_treasury_sweep(numeric), public.admin_treasury_admin()
  from public, anon, authenticated;
grant execute on function public._treasury_balance(), public._treasury_gas_per_day(timestamptz), public._treasury_profit(text, bigint, bigint),
  public.treasury_sync(), public.treasury_carve(timestamptz), public.treasury_status(bigint, timestamptz) to service_role;
grant execute on function public.treasury_overview() to anon, authenticated, service_role;
grant execute on function public.admin_treasury_start(), public.admin_treasury_settings(numeric, boolean, numeric, int),
  public.admin_treasury_record_render(numeric, date, text, text, boolean), public.admin_treasury_cancel_render(bigint),
  public.admin_treasury_sweep(numeric), public.admin_treasury_admin() to authenticated;

-- Every 5 minutes: record gas top-ups and match Render withdrawals, even when nobody is looking.
do $$ begin perform cron.unschedule('treasury-sync'); exception when others then null; end $$;
select cron.schedule('treasury-sync', '*/5 * * * *', $cron$ select public.treasury_sync() $cron$);
