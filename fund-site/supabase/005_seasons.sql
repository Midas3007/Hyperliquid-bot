-- Seasons, a public overview for the landing page, masked leaderboards and the "Deposited" fix.
--
-- A season ends when the admin taps "Start new season": its numbers are saved in public.seasons, its ledger and
-- fund value history are moved to the archive tables, and everyone still holding units starts the new season
-- with their value carried over at a unit price of $1.00. Members who are not fee-exempt pay the manager fee on
-- their season profit at that point; it becomes the manager's units, like any other fee.
-- Safe to run more than once.

alter table public.settings add column if not exists season int not null default 1;
alter table public.settings add column if not exists season_started_at timestamptz;
update public.settings set season_started_at = coalesce(season_started_at, started_at::timestamptz, now()) where id = 1;
alter table public.members add column if not exists wallet text;
alter table public.members drop constraint if exists members_wallet_check;
alter table public.members add constraint members_wallet_check check (wallet is null or wallet ~ '^0x[0-9a-fA-F]{40}$');
alter table public.ledger add column if not exists opening boolean not null default false; -- carried over from the last season

create table if not exists public.seasons (
  number int primary key,
  started_at timestamptz,
  ended_at timestamptz not null default now(),
  summary jsonb not null
);
create table if not exists public.ledger_archive (season int not null, entry jsonb not null);
create table if not exists public.nav_archive (season int not null, value numeric, total_units numeric, recorded_at timestamptz, note text);
alter table public.seasons enable row level security;
alter table public.ledger_archive enable row level security;
alter table public.nav_archive enable row level security;
-- No policies: these are read only through the functions below.

-- ---------- helpers ----------
-- How a member appears on leaderboards: part of their wallet address if they have one, otherwise W•••D.
create or replace function public._mask(p uuid) returns text
language sql stable security definer set search_path = '' as $$
  select case when wallet is not null then left(wallet, 6) || '…' || right(wallet, 4)
              else upper(left(name, 1)) || '•••' || case when length(name) > 2 then right(name, 1) else '' end end
  from public.members where id = p
$$;

-- One member's position. "Deposited" counts only money the member put in this season: fee units the manager
-- earned and value carried over from an earlier season are reported separately.
create or replace function public.position_of(p_member uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  m public.members;
  s public.settings;
  tu numeric := public.total_units();
  price numeric := public.unit_price();
  u numeric; cap numeric; dep numeric; carried numeric; earned numeric; wd numeric; joined date;
  v_gross numeric; v_profit numeric; f numeric;
begin
  select * into m from public.members where id = p_member;
  select * into s from public.settings where id = 1;
  select coalesce(sum(units), 0), coalesce(sum(capital), 0),
         coalesce(sum(gross) filter (where kind = 'deposit' and fee_for is null and not opening), 0),
         coalesce(sum(gross) filter (where kind = 'deposit' and opening), 0),
         coalesce(sum(gross) filter (where kind = 'deposit' and fee_for is not null), 0),
         coalesce(sum(net_payout) filter (where kind = 'withdrawal'), 0),
         min(txn_date) filter (where kind = 'deposit')
    into u, cap, dep, carried, earned, wd, joined
    from public.ledger where member_id = p_member;
  -- the manager's founder deposit was booked at a different cost basis; report what it cost
  if m.is_admin then
    select coalesce(sum(capital), 0) into dep from public.ledger
      where member_id = p_member and kind = 'deposit' and fee_for is null and not opening;
  end if;
  v_gross := u * price;
  v_profit := v_gross - cap;
  f := case when m.fee_exempt then 0 else greatest(v_profit, 0) * s.fee_pct / 100 end;
  return jsonb_build_object(
    'id', m.id, 'name', m.name, 'username', m.username, 'is_admin', m.is_admin, 'fee_exempt', m.fee_exempt,
    'wallet', m.wallet, 'label', public._mask(m.id),
    'has_login', m.user_id is not null, 'disclaimer_accepted_at', m.disclaimer_accepted_at,
    'joined', joined, 'total_deposited', dep, 'carried_over', carried, 'fees_earned', earned, 'total_withdrawn', wd,
    'capital', cap, 'units', u, 'share', case when tu > 0 then u / tu else 0 end,
    'unit_price', price, 'gross_value', v_gross, 'gross_profit', v_profit,
    'fee_pct', case when m.fee_exempt then 0 else s.fee_pct end, 'fee', f,
    'net_value', v_gross - f, 'net_profit', v_gross - f - cap,
    'lock_until', case when joined is not null and s.lock_days > 0 then joined + s.lock_days end
  );
end $$;

-- This season's leaderboard: return after fees on everything a member put in (including value carried over),
-- counting what they were paid out plus what they hold now. The manager is not ranked.
create or replace function public._leaderboard() returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(r order by (r->>'roi')::numeric desc), '[]'::jsonb) from (
    select jsonb_build_object('member_id', p.id, 'active', p.u > 0.0000001,
             'roi', round(((p.paid + (public.position_of(p.id)->>'net_value')::numeric - p.dep) / p.dep * 100)::numeric, 2)) r
    from (select l.member_id id,
                 sum(l.capital) filter (where l.kind = 'deposit' and l.fee_for is null) dep,
                 coalesce(sum(l.net_payout) filter (where l.kind = 'withdrawal'), 0) paid,
                 sum(l.units) u
          from public.ledger l join public.members m on m.id = l.member_id
          where not m.is_admin group by l.member_id) p
    where p.dep > 0) x
$$;

-- Leaderboard rows as shown: masked name, return, and whether the row is the viewer's.
create or replace function public._board(p_rows jsonb, p_me uuid) returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('label', public._mask((r->>'member_id')::uuid), 'roi', (r->>'roi')::numeric,
           'active', coalesce((r->>'active')::boolean, false), 'me', p_me is not null and (r->>'member_id')::uuid = p_me)
           order by (r->>'roi')::numeric desc), '[]'::jsonb)
  from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) r
$$;

-- Past seasons with their leaderboards masked for the viewer.
create or replace function public._seasons_for(p_me uuid) returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg((s.summary - 'leaderboard') || jsonb_build_object('leaderboard', public._board(s.summary->'leaderboard', p_me))
           order by s.number desc), '[]'::jsonb)
  from public.seasons s
$$;

-- The current season's unit price, at most one point per hour.
create or replace function public._price_history() returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('t', t, 'value', value, 'unit_price', up) order by t), '[]'::jsonb)
  from (select distinct on (date_trunc('hour', recorded_at)) recorded_at t, value,
               case when total_units > 0 then value / total_units else 1 end up
        from public.nav_snapshots order by date_trunc('hour', recorded_at), recorded_at desc, id desc) h
$$;

revoke all on function public._mask(uuid), public._leaderboard(), public._board(jsonb, uuid), public._seasons_for(uuid),
  public._price_history() from public, anon, authenticated;

-- ---------- member summary ----------
create or replace function public.fund_summary() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  s public.settings;
  price numeric := public.unit_price();
  me uuid := public.my_member_id();
begin
  if me is null then raise exception 'Not a fund member'; end if;
  select * into s from public.settings where id = 1;
  return jsonb_build_object(
    'fund_name', s.fund_name, 'fee_pct', s.fee_pct, 'total_slots', s.total_slots,
    'lock_days', s.lock_days, 'early_exit_pct', s.early_exit_pct,
    'bot_wallet', s.bot_wallet, 'started_at', s.started_at,
    'season', s.season, 'season_started_at', s.season_started_at,
    'members', (select count(*) from (select member_id from public.ledger group by member_id having sum(units) > 0) a),
    'capital', (select coalesce(sum(capital), 0) from public.ledger),
    'total_deposited', (select coalesce(sum(gross), 0) from public.ledger where kind = 'deposit' and fee_for is null and not opening),
    'total_withdrawn', (select coalesce(sum(net_payout), 0) from public.ledger where kind = 'withdrawal'),
    'total_units', public.total_units(), 'current_value', coalesce(public.current_value(), 0), 'unit_price', price,
    'last_updated', (select max(recorded_at) from public.nav_snapshots),
    'fees_accrued', (
      select coalesce(sum(greatest(p.units * price - p.cap, 0)), 0) * s.fee_pct / 100
      from (select l.member_id, sum(l.units) units, sum(l.capital) cap
            from public.ledger l join public.members m on m.id = l.member_id
            where not m.fee_exempt group by l.member_id) p),
    'fees_collected', (select coalesce(sum(fee), 0) from public.ledger),
    'history', (
      select coalesce(jsonb_agg(jsonb_build_object('t', recorded_at, 'value', value,
               'unit_price', case when total_units > 0 then value / total_units else 1 end) order by recorded_at, id), '[]'::jsonb)
      from public.nav_snapshots),
    'leaderboard', public._board(public._leaderboard(), me),
    'seasons', public._seasons_for(me)
  );
end $$;

-- ---------- public overview (landing page, no login) ----------
-- Only totals, returns and masked leaderboards: no names, amounts per member, or wallet address of the pool.
create or replace function public.public_overview() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare s public.settings;
begin
  select * into s from public.settings where id = 1;
  return jsonb_build_object(
    'fund_name', s.fund_name, 'fee_pct', s.fee_pct, 'total_slots', s.total_slots,
    'members', (select count(*) from (select member_id from public.ledger group by member_id having sum(units) > 0) a),
    'season', s.season, 'season_started_at', s.season_started_at,
    'current_value', coalesce(public.current_value(), 0), 'unit_price', public.unit_price(),
    'last_updated', (select max(recorded_at) from public.nav_snapshots),
    'history', public._price_history(),
    'leaderboard', public._board(public._leaderboard(), null),
    'seasons', public._seasons_for(null)
  );
end $$;
revoke all on function public.public_overview() from public;
grant execute on function public.public_overview() to anon, authenticated;

-- ---------- ending a season ----------
-- p_value: the pool wallet's live value right now. p_trades: the season's trading totals as the dashboard shows them.
create or replace function public._start_season(p_value numeric, p_trades jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  s public.settings;
  admin_id uuid := public._admin_member();
  tu numeric := public.total_units();
  price numeric;
  summary jsonb;
  h record;
  fees numeric := 0;
  total numeric := 0;
begin
  select * into s from public.settings where id = 1 for update;
  if p_value is null or p_value <= 0 then raise exception 'The fund value must be above zero'; end if;
  if tu <= 0 then raise exception 'Nobody holds units; there is nothing to carry over'; end if;
  perform public._record_value(p_value, 'Season ' || s.season || ' final value');
  price := p_value / tu;

  summary := jsonb_build_object(
    'number', s.season, 'name', 'Season ' || s.season,
    'started_at', s.season_started_at, 'ended_at', now(),
    'end_value', p_value, 'end_unit_price', round(price, 6), 'return_pct', round((price - 1) * 100, 2),
    'deposited', (select coalesce(sum(case when m.is_admin then l.capital else l.gross end), 0) from public.ledger l
                    join public.members m on m.id = l.member_id where l.kind = 'deposit' and l.fee_for is null),
    'paid_out', (select coalesce(sum(net_payout), 0) from public.ledger where kind = 'withdrawal'),
    'fees_collected', (select coalesce(sum(fee), 0) from public.ledger),
    'members', (select count(distinct l.member_id) from public.ledger l join public.members m on m.id = l.member_id
                  where not m.is_admin and l.kind = 'deposit'),
    'members_paid_out', (select count(distinct l.member_id) from public.ledger l join public.members m on m.id = l.member_id
                  where not m.is_admin and l.kind = 'withdrawal'),
    'leaderboard', public._leaderboard(),
    'trades', coalesce(p_trades, 'null'::jsonb),
    'history', public._price_history());

  insert into public.seasons (number, started_at, ended_at, summary) values (s.season, s.season_started_at, now(), summary);
  insert into public.ledger_archive (season, entry) select s.season, to_jsonb(l) from public.ledger l order by seq;
  insert into public.nav_archive (season, value, total_units, recorded_at, note)
    select s.season, value, total_units, recorded_at, note from public.nav_snapshots order by recorded_at, id;

  -- What each holder carries over: their value after the manager fee on this season's profit.
  create temp table carry on commit drop as
    select p.id member_id, (public.position_of(p.id)->>'net_value')::numeric net, (public.position_of(p.id)->>'fee')::numeric fee
    from (select member_id id from public.ledger group by member_id having sum(units) > 0.0000001) p;

  delete from public.ledger;
  delete from public.nav_snapshots;

  for h in select * from carry where net > 0.005 loop
    insert into public.ledger (member_id, kind, txn_date, fund_value_before, unit_price, units, capital, gross, opening, note)
    values (h.member_id, 'deposit', current_date, null, 1, round(h.net, 6), round(h.net, 6), round(h.net, 6), true,
            'Carried over from Season ' || s.season);
    fees := fees + h.fee;
    total := total + round(h.net, 6);
  end loop;
  if fees > 0.000001 and admin_id is not null then
    insert into public.ledger (member_id, kind, txn_date, fund_value_before, unit_price, units, capital, gross, opening, note)
    values (admin_id, 'deposit', current_date, null, 1, round(fees, 6), 0, round(fees, 6), true, 'Season ' || s.season || ' fees');
    total := total + round(fees, 6);
  end if;

  perform public._record_value(p_value, 'Season ' || (s.season + 1) || ' starts at $1.00');
  update public.settings set season = s.season + 1, season_started_at = now(), started_at = current_date, updated_at = now() where id = 1;
  return jsonb_build_object('ended', s.season, 'return_pct', round((price - 1) * 100, 2), 'carried_over', total, 'fees', fees,
                            'new_unit_price', case when public.total_units() > 0 then p_value / public.total_units() else 1 end);
end $$;
revoke all on function public._start_season(numeric, jsonb) from public, anon, authenticated;

create or replace function public.admin_start_season(p_value numeric, p_trades jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  return public._start_season(p_value, p_trades);
end $$;

-- A member's wallet address, used to label them on leaderboards (and later to link the fund contract).
create or replace function public.admin_set_wallet(p_member uuid, p_wallet text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if p_wallet is not null and trim(p_wallet) <> '' and trim(p_wallet) !~ '^0x[0-9a-fA-F]{40}$' then raise exception 'That is not a wallet address'; end if;
  update public.members set wallet = nullif(lower(trim(p_wallet)), '') where id = p_member;
end $$;

revoke all on function public.admin_start_season(numeric, jsonb), public.admin_set_wallet(uuid, text) from public, anon;
grant execute on function public.admin_start_season(numeric, jsonb), public.admin_set_wallet(uuid, text) to authenticated;
