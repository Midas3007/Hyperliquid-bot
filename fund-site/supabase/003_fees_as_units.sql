-- Manager fees stay in the pool as the admin's units instead of leaving the pool wallet.
--
-- On a withdrawal the member is paid gross - penalty - fee. The fee is then recorded as a deposit by the
-- admin, buying units at the unit price right after the withdrawal, so it belongs to the admin and is not
-- shared out as profit. The one-time catch-up at the bottom does the same for fees collected before this change.

alter table public.ledger add column if not exists fee_for uuid references public.ledger (id) on delete cascade;

create or replace function public._admin_member() returns uuid
language sql stable security definer set search_path = '' as $$
  select id from public.members where is_admin order by created_at limit 1
$$;
revoke all on function public._admin_member() from public, anon, authenticated;

create or replace function public._withdraw(p_member uuid, p_amount numeric, p_on date, p_value_before numeric, p_commit boolean)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  m public.members;
  s public.settings;
  tu numeric := public.total_units();
  price numeric; mu numeric; mcap numeric; joined date;
  sell_units numeric; v_gross numeric; v_cost numeric; pen numeric; f numeric; v_early boolean;
  w_id uuid; d public.ledger; admin_id uuid := public._admin_member();
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
    values (p_member, 'withdrawal', p_on, p_value_before, price, -sell_units, -v_cost, v_gross, pen, f, v_gross - pen - f)
    returning id into w_id;
    perform public._record_value(greatest(p_value_before - (v_gross - pen), 0), 'Withdrawal by ' || m.name);
    -- The fee never leaves the wallet: it becomes the admin's units at the post-withdrawal price.
    if f > 0 and admin_id is not null then
      d := public._add_deposit(admin_id, round(f, 6), p_on, greatest(p_value_before - (v_gross - pen), 0));
      update public.ledger set fee_for = w_id, note = 'Fee from ' || m.name || ' reinvested' where id = d.id;
    end if;
  end if;

  return jsonb_build_object(
    'member', m.name, 'unit_price', price, 'units_sold', sell_units, 'units_left', mu - sell_units,
    'full_exit', sell_units = mu, 'gross', v_gross, 'cost', v_cost, 'profit', v_gross - v_cost,
    'early', v_early, 'penalty_pct', case when v_early then s.early_exit_pct else 0 end, 'penalty', pen,
    'fee_pct', case when m.fee_exempt then 0 else s.fee_pct end, 'fee', f,
    'net_payout', v_gross - pen - f, 'leaves_fund', v_gross - pen - f,
    'fund_value_after', greatest(p_value_before - (v_gross - pen - f), 0));
end $$;
revoke all on function public._withdraw(uuid, numeric, date, numeric, boolean) from public, anon, authenticated;

-- Undo the latest entry; a reinvested fee is undone together with the withdrawal it came from.
create or replace function public.admin_undo_last() returns void
language plpgsql security definer set search_path = '' as $$
declare r public.ledger; w public.ledger;
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  select * into r from public.ledger order by seq desc limit 1;
  if r.id is null then raise exception 'Nothing to undo'; end if;
  if r.fee_for is not null and coalesce(r.note, '') not like '%catch-up%' then
    select * into w from public.ledger where id = r.fee_for;
    delete from public.ledger where id = r.id;
    delete from public.ledger where id = w.id;
    perform public._record_value(greatest(coalesce(public.current_value(), 0) - r.gross + (w.gross - w.penalty), 0), 'Undo withdrawal');
    return;
  end if;
  delete from public.ledger where id = r.id;
  perform public._record_value(
    greatest(coalesce(public.current_value(), 0) + case when r.kind = 'deposit' then -r.gross else r.gross - r.penalty end, 0),
    'Undo ' || r.kind);
end $$;

-- admin_overview: show the note so reinvested fees are labelled in the ledger.
create or replace function public.admin_overview() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.is_admin() then raise exception 'Admins only'; end if;
  return jsonb_build_object(
    'members', (select coalesce(jsonb_agg(public.position_of(id) order by created_at), '[]'::jsonb) from public.members),
    'ledger', (
      select coalesce(jsonb_agg(jsonb_build_object('id', l.id, 'seq', l.seq, 'member', m.name, 'kind', l.kind,
               'date', l.txn_date, 'fund_value_before', l.fund_value_before, 'unit_price', l.unit_price,
               'units', l.units, 'gross', l.gross, 'penalty', l.penalty, 'fee', l.fee, 'net_payout', l.net_payout,
               'note', l.note, 'created_at', l.created_at) order by l.seq desc), '[]'::jsonb)
      from public.ledger l join public.members m on m.id = l.member_id),
    'snapshots', (
      select coalesce(jsonb_agg(jsonb_build_object('id', id, 'value', value, 'recorded_at', recorded_at, 'note', note)
               order by recorded_at desc, id desc), '[]'::jsonb)
      from (select * from public.nav_snapshots order by recorded_at desc, id desc limit 50) s)
  );
end $$;

-- One-time catch-up: fees already collected (still sitting in the pool wallet) become the admin's units now,
-- priced from the fund value without them, so they stop counting as everyone's profit.
do $$
declare
  w public.ledger;
  d public.ledger;
  admin_id uuid := public._admin_member();
begin
  if admin_id is null then return; end if;
  for w in
    select l.* from public.ledger l
    where l.kind = 'withdrawal' and l.fee > 0
      and not exists (select 1 from public.ledger x where x.fee_for = l.id)
    order by l.seq
  loop
    d := public._add_deposit(admin_id, round(w.fee, 6), current_date, greatest(coalesce(public.current_value(), 0) - w.fee, 0));
    update public.ledger set fee_for = w.id, note = 'Fee reinvested (catch-up)' where id = d.id;
  end loop;
end $$;
