-- Midas × Kiyoshi Fund: schema, row-level security and RPCs.
-- Unit accounting mirrors the "MIDAS x AXIØN FUND" spreadsheet: every deposit buys units at the
-- unit price just before it lands (founders buy at 1.00), so members only share in P&L made after they join.

create table public.settings (
  id int primary key default 1 check (id = 1),
  fund_name text not null default 'Midas × Kiyoshi Fund',
  fee_pct numeric(5,2) not null default 10 check (fee_pct >= 0 and fee_pct < 100),
  total_slots int not null default 15,
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

create table public.deposits (
  id uuid primary key default gen_random_uuid(),
  seq bigserial unique,
  member_id uuid not null references public.members (id) on delete restrict,
  amount numeric(18,2) not null check (amount > 0),
  deposited_on date,
  fund_value_before numeric(18,2) check (fund_value_before >= 0), -- null = founder, buys at 1.00
  unit_price numeric(24,10) not null check (unit_price > 0),
  units numeric(28,10) not null check (units > 0),
  created_at timestamptz not null default now()
);
create index on public.deposits (member_id);

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
alter table public.deposits enable row level security;
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
  select coalesce(sum(units), 0) from public.deposits
$$;

create function public.current_value() returns numeric
language sql stable security definer set search_path = '' as $$
  select value from public.nav_snapshots order by recorded_at desc, id desc limit 1
$$;

-- ---------- policies ----------
create policy settings_read on public.settings for select to authenticated using (public.my_member_id() is not null);
create policy settings_admin on public.settings for update to authenticated using (public.is_admin()) with check (public.is_admin());

create policy members_read on public.members for select to authenticated using (user_id = auth.uid() or public.is_admin());
create policy members_admin_update on public.members for update to authenticated using (public.is_admin()) with check (public.is_admin());

create policy deposits_read on public.deposits for select to authenticated using (member_id = public.my_member_id() or public.is_admin());

create policy nav_read on public.nav_snapshots for select to authenticated using (public.my_member_id() is not null);

-- ---------- internal writers (not callable by clients) ----------
create function public._record_value(p_value numeric, p_note text, p_at timestamptz default now()) returns void
language sql security definer set search_path = '' as $$
  insert into public.nav_snapshots (value, total_units, recorded_at, note)
  values (p_value, public.total_units(), p_at, p_note)
$$;

create function public._add_deposit(p_member uuid, p_amount numeric, p_on date, p_value_before numeric) returns public.deposits
language plpgsql security definer set search_path = '' as $$
declare
  units_before numeric := public.total_units();
  price numeric;
  d public.deposits;
begin
  if p_amount is null or p_amount <= 0 then raise exception 'Amount must be positive'; end if;
  price := case when p_value_before is null or units_before = 0 then 1 else p_value_before / units_before end;
  if price <= 0 then raise exception 'Fund value before deposit must be above zero'; end if;
  insert into public.deposits (member_id, amount, deposited_on, fund_value_before, unit_price, units)
  values (p_member, p_amount, p_on, p_value_before, price, p_amount / price)
  returning * into d;
  perform public._record_value(coalesce(p_value_before, public.current_value(), 0) + p_amount,
    'Deposit by ' || (select name from public.members where id = p_member));
  return d;
end $$;

revoke all on function public._record_value(numeric, text, timestamptz) from public, anon, authenticated;
revoke all on function public._add_deposit(uuid, numeric, date, numeric) from public, anon, authenticated;

-- ---------- member RPCs ----------
create function public.fund_summary() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  s public.settings;
  tu numeric := public.total_units();
  cv numeric := public.current_value();
  price numeric;
begin
  if public.my_member_id() is null then raise exception 'Not a fund member'; end if;
  select * into s from public.settings where id = 1;
  price := case when tu > 0 then cv / tu else 1 end;
  return jsonb_build_object(
    'fund_name', s.fund_name, 'fee_pct', s.fee_pct, 'total_slots', s.total_slots,
    'bot_wallet', s.bot_wallet, 'started_at', s.started_at,
    'members', (select count(distinct member_id) from public.deposits),
    'total_deposited', (select coalesce(sum(amount), 0) from public.deposits),
    'total_units', tu, 'current_value', coalesce(cv, 0), 'unit_price', price,
    'last_updated', (select max(recorded_at) from public.nav_snapshots),
    'fees_accrued', (
      select coalesce(sum(greatest(p.units * price - p.dep, 0)), 0) * s.fee_pct / 100
      from (select d.member_id, sum(d.units) units, sum(d.amount) dep
            from public.deposits d join public.members m on m.id = d.member_id
            where not m.fee_exempt group by d.member_id) p),
    'history', (
      select coalesce(jsonb_agg(jsonb_build_object('t', recorded_at, 'value', value,
               'unit_price', case when total_units > 0 then value / total_units else 1 end) order by recorded_at, id), '[]'::jsonb)
      from public.nav_snapshots)
  );
end $$;

create function public.my_position() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  m public.members;
  tu numeric := public.total_units();
  price numeric;
  fee numeric;
  u numeric; dep numeric; gross numeric; profit numeric; f numeric;
begin
  select * into m from public.members where user_id = auth.uid();
  if m.id is null then raise exception 'Not a fund member'; end if;
  price := case when tu > 0 then public.current_value() / tu else 1 end;
  select fee_pct into fee from public.settings where id = 1;
  select coalesce(sum(units), 0), coalesce(sum(amount), 0) into u, dep from public.deposits where member_id = m.id;
  gross := u * price;
  profit := gross - dep;
  f := case when m.fee_exempt then 0 else greatest(profit, 0) * fee / 100 end;
  return jsonb_build_object(
    'name', m.name, 'username', m.username, 'is_admin', m.is_admin, 'fee_exempt', m.fee_exempt,
    'disclaimer_accepted_at', m.disclaimer_accepted_at,
    'deposited', dep, 'units', u, 'share', case when tu > 0 then u / tu else 0 end,
    'unit_price', price, 'gross_value', gross, 'gross_profit', profit,
    'fee_pct', case when m.fee_exempt then 0 else fee end, 'fee', f,
    'net_value', gross - f, 'net_profit', gross - f - dep,
    'deposits', (select coalesce(jsonb_agg(jsonb_build_object('amount', amount, 'on', deposited_on,
                  'unit_price', unit_price, 'units', units) order by seq), '[]'::jsonb)
                 from public.deposits where member_id = m.id)
  );
end $$;

create function public.accept_disclaimer() returns void
language sql security definer set search_path = '' as $$
  update public.members set disclaimer_accepted_at = now() where user_id = auth.uid() and disclaimer_accepted_at is null
$$;

-- ---------- admin RPCs ----------
create function public.admin_overview() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  tu numeric := public.total_units();
  price numeric;
  fee numeric;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  price := case when tu > 0 then public.current_value() / tu else 1 end;
  select fee_pct into fee from public.settings where id = 1;
  return jsonb_build_object(
    'members', (
      select coalesce(jsonb_agg(row_to_json(x) order by x.created_at), '[]'::jsonb) from (
        select m.id, m.name, m.username, m.is_admin, m.fee_exempt, m.user_id is not null as has_login,
               m.disclaimer_accepted_at, m.created_at,
               coalesce(sum(d.amount), 0) deposited, coalesce(sum(d.units), 0) units,
               case when tu > 0 then coalesce(sum(d.units), 0) / tu else 0 end share,
               coalesce(sum(d.units), 0) * price gross_value,
               case when m.fee_exempt then 0 else greatest(coalesce(sum(d.units), 0) * price - coalesce(sum(d.amount), 0), 0) * fee / 100 end fee
        from public.members m left join public.deposits d on d.member_id = m.id
        group by m.id) x),
    'deposits', (
      select coalesce(jsonb_agg(jsonb_build_object('id', d.id, 'seq', d.seq, 'member', m.name, 'amount', d.amount,
               'on', d.deposited_on, 'fund_value_before', d.fund_value_before, 'unit_price', d.unit_price,
               'units', d.units, 'created_at', d.created_at) order by d.seq desc), '[]'::jsonb)
      from public.deposits d join public.members m on m.id = d.member_id),
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
  d public.deposits;
  slots int;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if not exists (select 1 from public.members where id = p_member) then raise exception 'Unknown member'; end if;
  select total_slots into slots from public.settings where id = 1;
  if (select count(*) from public.deposits) >= slots then raise exception 'All % slots are used', slots; end if;
  d := public._add_deposit(p_member, p_amount, p_on, p_value_before);
  return to_jsonb(d);
end $$;

create function public.admin_set_value(p_value numeric, p_note text default null) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  if p_value is null or p_value < 0 then raise exception 'Value must be zero or more'; end if;
  perform public._record_value(p_value, coalesce(nullif(trim(p_note), ''), 'Fund value update'));
end $$;

-- Only the latest deposit can be undone: every later deposit's unit price depends on the ones before it.
create function public.admin_undo_last_deposit() returns void
language plpgsql security definer set search_path = '' as $$
declare d public.deposits;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into d from public.deposits order by seq desc limit 1;
  if d.id is null then raise exception 'No deposits'; end if;
  delete from public.deposits where id = d.id;
  perform public._record_value(greatest(coalesce(public.current_value(), 0) - d.amount, 0), 'Undo deposit');
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
  if exists (select 1 from public.deposits where member_id = p_member) then raise exception 'Member has deposits; undo them first'; end if;
  if exists (select 1 from public.members where id = p_member and user_id is not null) then raise exception 'Remove the login first'; end if;
  delete from public.members where id = p_member and not is_admin;
end $$;

revoke all on function public.fund_summary(), public.my_position(), public.accept_disclaimer(),
  public.admin_overview(), public.admin_add_member(text, boolean), public.admin_add_deposit(uuid, numeric, date, numeric),
  public.admin_set_value(numeric, text), public.admin_undo_last_deposit(), public.admin_delete_snapshot(bigint),
  public.admin_delete_member(uuid) from public, anon;
grant execute on function public.fund_summary(), public.my_position(), public.accept_disclaimer(),
  public.admin_overview(), public.admin_add_member(text, boolean), public.admin_add_deposit(uuid, numeric, date, numeric),
  public.admin_set_value(numeric, text), public.admin_undo_last_deposit(), public.admin_delete_snapshot(bigint),
  public.admin_delete_member(uuid) to authenticated;
revoke all on function public.is_admin(), public.my_member_id(), public.total_units(), public.current_value() from public, anon;
grant execute on function public.is_admin(), public.my_member_id(), public.total_units(), public.current_value() to authenticated;
