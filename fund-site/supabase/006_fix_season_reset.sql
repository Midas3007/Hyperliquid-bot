-- Fix: "Start new season" failed with "DELETE requires a WHERE clause" (the API refuses bare deletes).
-- Paste into Supabase, SQL Editor, New query, Run. Safe to run more than once.

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

  -- "where true": the API's safety setting refuses a delete without a where clause
  delete from public.ledger where true;
  delete from public.nav_snapshots where true;

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
