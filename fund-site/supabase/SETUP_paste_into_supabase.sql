-- Midas × Kiyoshi Fund: schema, row-level security and RPCs.
--
-- Unit accounting mirrors the "MIDAS x AXIØN FUND" spreadsheet:
--   unit price = fund value / total units
--   a deposit buys units at the unit price just before it lands (founders buy at 1.00),
--   so members only share in profit or loss made after they join.
--
-- Withdrawals sell units back at the unit price just before the money leaves:
--   gross       = units sold × unit price
--   cost        = the part of the member's capital those units represent (average cost)
--   penalty     = gross × early-exit %, only if the member joined less than lock_days ago;
--                 it stays in the fund, so it goes to the members who remain
--   fee         = manager fee % × (gross − cost), only when that is a profit
--   member gets = gross − penalty − fee
--   fund value falls by gross − penalty (the member's payout plus the manager's fee)

create table public.settings (
  id int primary key default 1 check (id = 1),
  fund_name text not null default 'Midas × Kiyoshi Fund',
  fee_pct numeric(5,2) not null default 10 check (fee_pct >= 0 and fee_pct < 100),
  total_slots int not null default 15,
  lock_days int not null default 0 check (lock_days >= 0),
  early_exit_pct numeric(5,2) not null default 0 check (early_exit_pct >= 0 and early_exit_pct < 100),
  bot_wallet text,
  started_at date,
  updated_at timestamptz not null default now()
);

create table public.members (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  user_id uuid unique references auth.users (id) on delete set null,
  username text unique,
  is_admin boolean not null default false,
  fee_exempt boolean not null default false,
  disclaimer_accepted_at timestamptz,
  created_at timestamptz not null default now()
);

-- Every deposit and withdrawal, in order. Units and capital are signed, so a member's position is a plain sum.
create table public.ledger (
  id uuid primary key default gen_random_uuid(),
  seq bigserial unique,
  member_id uuid not null references public.members (id) on delete restrict,
  kind text not null check (kind in ('deposit', 'withdrawal')),
  txn_date date,
  fund_value_before numeric(18,2) check (fund_value_before >= 0), -- null on a founder deposit (buys at 1.00)
  unit_price numeric(24,10) not null check (unit_price > 0),
  units numeric(28,10) not null,         -- + bought, − sold
  capital numeric(18,6) not null,        -- + deposited, − cost of the units sold
  gross numeric(18,6) not null check (gross > 0), -- deposit amount, or value of the units sold
  penalty numeric(18,6) not null default 0,
  fee numeric(18,6) not null default 0,
  net_payout numeric(18,6),              -- what the member receives on a withdrawal
  note text,
  created_at timestamptz not null default now(),
  check ((kind = 'deposit' and units > 0 and capital > 0) or (kind = 'withdrawal' and units < 0 and capital <= 0))
);
create index on public.ledger (member_id);

create table public.nav_snapshots (
  id bigserial primary key,
  value numeric(18,2) not null check (value >= 0),
  total_units numeric(28,10) not null,
  recorded_at timestamptz not null default now(),
  note text
);
create index on public.nav_snapshots (recorded_at);

alter table public.settings enable row level security;
alter table public.members enable row level security;
alter table public.ledger enable row level security;
alter table public.nav_snapshots enable row level security;

-- ---------- helpers ----------
create function public.is_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.members where user_id = auth.uid() and is_admin)
$$;

create function public.my_member_id() returns uuid
language sql stable security definer set search_path = '' as $$
  select id from public.members where user_id = auth.uid()
$$;

create function public.total_units() returns numeric
language sql stable security definer set search_path = '' as $$
  select coalesce(sum(units), 0) from public.ledger
$$;

create function public.current_value() returns numeric
language sql stable security definer set search_path = '' as $$
  select value from public.nav_snapshots order by recorded_at desc, id desc limit 1
$$;

create function public.unit_price() returns numeric
language sql stable security definer set search_path = '' as $$
  select case when public.total_units() > 0 then coalesce(public.current_value(), 0) / public.total_units() else 1 end
$$;

-- One member's position at the current unit price, including the manager fee they would pay on exit today.
create function public.position_of(p_member uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  m public.members;
  s public.settings;
  tu numeric := public.total_units();
  price numeric := public.unit_price();
  u numeric; cap numeric; dep numeric; wd numeric; joined date;
  v_gross numeric; v_profit numeric; f numeric;
begin
  select * into m from public.members where id = p_member;
  select * into s from public.settings where id = 1;
  select coalesce(sum(units), 0), coalesce(sum(capital), 0),
         coalesce(sum(gross) filter (where kind = 'deposit'), 0),
         coalesce(sum(net_payout) filter (where kind = 'withdrawal'), 0),
         min(txn_date) filter (where kind = 'deposit')
    into u, cap, dep, wd, joined
    from public.ledger where member_id = p_member;
  v_gross := u * price;
  v_profit := v_gross - cap;
  f := case when m.fee_exempt then 0 else greatest(v_profit, 0) * s.fee_pct / 100 end;
  return jsonb_build_object(
    'id', m.id, 'name', m.name, 'username', m.username, 'is_admin', m.is_admin, 'fee_exempt', m.fee_exempt,
    'has_login', m.user_id is not null, 'disclaimer_accepted_at', m.disclaimer_accepted_at,
    'joined', joined, 'total_deposited', dep, 'total_withdrawn', wd,
    'capital', cap, 'units', u, 'share', case when tu > 0 then u / tu else 0 end,
    'unit_price', price, 'gross_value', v_gross, 'gross_profit', v_profit,
    'fee_pct', case when m.fee_exempt then 0 else s.fee_pct end, 'fee', f,
    'net_value', v_gross - f, 'net_profit', v_gross - f - cap,
    'lock_until', case when joined is not null and s.lock_days > 0 then joined + s.lock_days end
  );
end $$;

-- ---------- policies ----------
create policy settings_read on public.settings for select to authenticated using (public.my_member_id() is not null);
create policy settings_admin on public.settings for update to authenticated using (public.is_admin()) with check (public.is_admin());

create policy members_read on public.members for select to authenticated using (user_id = auth.uid() or public.is_admin());
create policy members_admin_update on public.members for update to authenticated using (public.is_admin()) with check (public.is_admin());

create policy ledger_read on public.ledger for select to authenticated using (member_id = public.my_member_id() or public.is_admin());

create policy nav_read on public.nav_snapshots for select to authenticated using (public.my_member_id() is not null);

-- ---------- internal writers (not callable by clients) ----------
create function public._record_value(p_value numeric, p_note text, p_at timestamptz default now()) returns void
language sql security definer set search_path = '' as $$
  insert into public.nav_snapshots (value, total_units, recorded_at, note)
  values (p_value, public.total_units(), p_at, p_note)
$$;

create function public._add_deposit(p_member uuid, p_amount numeric, p_on date, p_value_before numeric) returns public.ledger
language plpgsql security definer set search_path = '' as $$
declare
  units_before numeric := public.total_units();
  price numeric;
  r public.ledger;
begin
  if p_amount is null or p_amount <= 0 then raise exception 'Amount must be positive'; end if;
  price := case when p_value_before is null or units_before = 0 then 1 else p_value_before / units_before end;
  if price <= 0 then raise exception 'Fund value before deposit must be above zero'; end if;
  insert into public.ledger (member_id, kind, txn_date, fund_value_before, unit_price, units, capital, gross)
  values (p_member, 'deposit', p_on, p_value_before, price, p_amount / price, p_amount, p_amount)
  returning * into r;
  perform public._record_value(coalesce(p_value_before, public.current_value(), 0) + p_amount,
    'Deposit by ' || (select name from public.members where id = p_member));
  return r;
end $$;

-- p_amount: value of units to sell, before penalty and fee. null = withdraw everything.
-- p_commit false only previews the numbers.
create function public._withdraw(p_member uuid, p_amount numeric, p_on date, p_value_before numeric, p_commit boolean)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  m public.members;
  s public.settings;
  tu numeric := public.total_units();
  price numeric; mu numeric; mcap numeric; joined date;
  sell_units numeric; v_gross numeric; v_cost numeric; pen numeric; f numeric; v_early boolean;
begin
  select * into m from public.members where id = p_member;
  if m.id is null then raise exception 'Unknown member'; end if;
  select * into s from public.settings where id = 1;
  if p_value_before is null or p_value_before <= 0 then raise exception 'Enter the fund value right before this withdrawal'; end if;
  select coalesce(sum(units), 0), coalesce(sum(capital), 0), min(txn_date) filter (where kind = 'deposit')
    into mu, mcap, joined from public.ledger where member_id = p_member;
  if mu <= 0 then raise exception '% has nothing to withdraw', m.name; end if;
  price := p_value_before / tu;

  if p_amount is null or p_amount >= mu * price - 0.005 then
    sell_units := mu;                               -- everything (absorbs rounding)
  else
    if p_amount <= 0 then raise exception 'Amount must be positive'; end if;
    sell_units := p_amount / price;
  end if;
  v_gross := sell_units * price;
  v_cost := mcap * sell_units / mu;                   -- average cost of the units sold
  v_early := s.lock_days > 0 and joined is not null and coalesce(p_on, current_date) < joined + s.lock_days;
  pen := case when v_early then v_gross * s.early_exit_pct / 100 else 0 end;
  f := case when m.fee_exempt then 0 else greatest(v_gross - v_cost, 0) * s.fee_pct / 100 end;

  if p_commit then
    insert into public.ledger (member_id, kind, txn_date, fund_value_before, unit_price, units, capital, gross, penalty, fee, net_payout)
    values (p_member, 'withdrawal', p_on, p_value_before, price, -sell_units, -v_cost, v_gross, pen, f, v_gross - pen - f);
    perform public._record_value(greatest(p_value_before - (v_gross - pen), 0), 'Withdrawal by ' || m.name);
  end if;

  return jsonb_build_object(
    'member', m.name, 'unit_price', price, 'units_sold', sell_units, 'units_left', mu - sell_units,
    'full_exit', sell_units = mu, 'gross', v_gross, 'cost', v_cost, 'profit', v_gross - v_cost,
    'early', v_early, 'penalty_pct', case when v_early then s.early_exit_pct else 0 end, 'penalty', pen,
    'fee_pct', case when m.fee_exempt then 0 else s.fee_pct end, 'fee', f,
    'net_payout', v_gross - pen - f, 'leaves_fund', v_gross - pen,
    'fund_value_after', greatest(p_value_before - (v_gross - pen), 0));
end $$;

revoke all on function public._record_value(numeric, text, timestamptz) from public, anon, authenticated;
revoke all on function public._add_deposit(uuid, numeric, date, numeric) from public, anon, authenticated;
revoke all on function public._withdraw(uuid, numeric, date, numeric, boolean) from public, anon, authenticated;
revoke all on function public.position_of(uuid) from public, anon, authenticated;

-- ---------- member RPCs ----------
create function public.fund_summary() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  s public.settings;
  price numeric := public.unit_price();
begin
  if public.my_member_id() is null then raise exception 'Not a fund member'; end if;
  select * into s from public.settings where id = 1;
  return jsonb_build_object(
    'fund_name', s.fund_name, 'fee_pct', s.fee_pct, 'total_slots', s.total_slots,
    'lock_days', s.lock_days, 'early_exit_pct', s.early_exit_pct,
    'bot_wallet', s.bot_wallet, 'started_at', s.started_at,
    'members', (select count(*) from (select member_id from public.ledger group by member_id having sum(units) > 0) a),
    'capital', (select coalesce(sum(capital), 0) from public.ledger),
    'total_deposited', (select coalesce(sum(gross), 0) from public.ledger where kind = 'deposit'),
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
      from public.nav_snapshots)
  );
end $$;

create function public.my_position() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare me uuid := public.my_member_id();
begin
  if me is null then raise exception 'Not a fund member'; end if;
  return public.position_of(me) || jsonb_build_object('ledger', (
    select coalesce(jsonb_agg(jsonb_build_object('kind', kind, 'date', txn_date, 'unit_price', unit_price, 'units', units,
             'gross', gross, 'penalty', penalty, 'fee', fee, 'net_payout', net_payout) order by seq), '[]'::jsonb)
    from public.ledger where member_id = me));
end $$;

create function public.accept_disclaimer() returns void
language sql security definer set search_path = '' as $$
  update public.members set disclaimer_accepted_at = now() where user_id = auth.uid() and disclaimer_accepted_at is null
$$;

-- ---------- admin RPCs ----------
create function public.admin_overview() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  return jsonb_build_object(
    'members', (select coalesce(jsonb_agg(public.position_of(id) order by created_at), '[]'::jsonb) from public.members),
    'ledger', (
      select coalesce(jsonb_agg(jsonb_build_object('id', l.id, 'seq', l.seq, 'member', m.name, 'kind', l.kind,
               'date', l.txn_date, 'fund_value_before', l.fund_value_before, 'unit_price', l.unit_price,
               'units', l.units, 'gross', l.gross, 'penalty', l.penalty, 'fee', l.fee, 'net_payout', l.net_payout,
               'created_at', l.created_at) order by l.seq desc), '[]'::jsonb)
      from public.ledger l join public.members m on m.id = l.member_id),
    'snapshots', (
      select coalesce(jsonb_agg(jsonb_build_object('id', id, 'value', value, 'recorded_at', recorded_at, 'note', note)
               order by recorded_at desc, id desc), '[]'::jsonb)
      from (select * from public.nav_snapshots order by recorded_at desc, id desc limit 50) s)
  );
end $$;

create function public.admin_add_member(p_name text, p_fee_exempt boolean default false) returns uuid
language plpgsql security definer set search_path = '' as $$
declare new_id uuid;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if coalesce(trim(p_name), '') = '' then raise exception 'Name is required'; end if;
  insert into public.members (name, fee_exempt) values (trim(p_name), coalesce(p_fee_exempt, false)) returning id into new_id;
  return new_id;
end $$;

create function public.admin_add_deposit(p_member uuid, p_amount numeric, p_on date, p_value_before numeric) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  slots int;
  holders int;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if not exists (select 1 from public.members where id = p_member) then raise exception 'Unknown member'; end if;
  select total_slots into slots from public.settings where id = 1;
  select count(*) into holders from (select member_id from public.ledger group by member_id having sum(units) > 0) a;
  if holders >= slots and not exists (select 1 from public.ledger where member_id = p_member group by member_id having sum(units) > 0) then
    raise exception 'All % slots are taken', slots;
  end if;
  return to_jsonb(public._add_deposit(p_member, p_amount, p_on, p_value_before));
end $$;

create function public.admin_preview_withdrawal(p_member uuid, p_amount numeric, p_on date, p_value_before numeric) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  return public._withdraw(p_member, p_amount, p_on, p_value_before, false);
end $$;

create function public.admin_withdraw(p_member uuid, p_amount numeric, p_on date, p_value_before numeric) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  return public._withdraw(p_member, p_amount, p_on, p_value_before, true);
end $$;

create function public.admin_set_value(p_value numeric, p_note text default null) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if p_value is null or p_value < 0 then raise exception 'Value must be zero or more'; end if;
  perform public._record_value(p_value, coalesce(nullif(trim(p_note), ''), 'Fund value update'));
end $$;

-- Only the latest entry can be undone: every later entry's unit price depends on the ones before it.
create function public.admin_undo_last() returns void
language plpgsql security definer set search_path = '' as $$
declare r public.ledger;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into r from public.ledger order by seq desc limit 1;
  if r.id is null then raise exception 'Nothing to undo'; end if;
  delete from public.ledger where id = r.id;
  perform public._record_value(
    greatest(coalesce(public.current_value(), 0) + case when r.kind = 'deposit' then -r.gross else r.gross - r.penalty end, 0),
    'Undo ' || r.kind);
end $$;

create function public.admin_delete_snapshot(p_id bigint) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if (select count(*) from public.nav_snapshots) <= 1 then raise exception 'Keep at least one value'; end if;
  delete from public.nav_snapshots where id = p_id;
end $$;

create function public.admin_delete_member(p_member uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if exists (select 1 from public.ledger where member_id = p_member) then raise exception 'Member has history in the ledger and cannot be deleted'; end if;
  if exists (select 1 from public.members where id = p_member and user_id is not null) then raise exception 'Remove the login first'; end if;
  delete from public.members where id = p_member and not is_admin;
end $$;

revoke all on function public.fund_summary(), public.my_position(), public.accept_disclaimer(),
  public.admin_overview(), public.admin_add_member(text, boolean), public.admin_add_deposit(uuid, numeric, date, numeric),
  public.admin_preview_withdrawal(uuid, numeric, date, numeric), public.admin_withdraw(uuid, numeric, date, numeric),
  public.admin_set_value(numeric, text), public.admin_undo_last(), public.admin_delete_snapshot(bigint),
  public.admin_delete_member(uuid) from public, anon;
grant execute on function public.fund_summary(), public.my_position(), public.accept_disclaimer(),
  public.admin_overview(), public.admin_add_member(text, boolean), public.admin_add_deposit(uuid, numeric, date, numeric),
  public.admin_preview_withdrawal(uuid, numeric, date, numeric), public.admin_withdraw(uuid, numeric, date, numeric),
  public.admin_set_value(numeric, text), public.admin_undo_last(), public.admin_delete_snapshot(bigint),
  public.admin_delete_member(uuid) to authenticated;
revoke all on function public.is_admin(), public.my_member_id(), public.total_units(), public.current_value(), public.unit_price()
  from public, anon;
grant execute on function public.is_admin(), public.my_member_id(), public.total_units(), public.current_value(), public.unit_price()
  to authenticated;
-- Starting data: the fund begins with only Midas (admin, no manager fee) and no deposits.
-- Midas records the first deposit from the admin page as a founder deposit (units at $1.00).
insert into public.settings (id, fund_name, fee_pct, total_slots, bot_wallet, started_at)
values (1, 'Midas × Kiyoshi Fund', 10, 15, '0x6cd37bc290c5c02ea92086d416a6a673bca581dc', current_date);

insert into public.members (name, is_admin, fee_exempt) values ('Midas', true, true);
