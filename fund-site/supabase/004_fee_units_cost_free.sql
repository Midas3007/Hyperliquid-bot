-- Fees the manager earns are profit, not money put in. Until now each reinvested fee was recorded as a deposit
-- with a cost equal to the fee, which made the manager's "capital invested" grow and their profit look smaller.
-- From now on fee units carry zero cost, and the manager's cost basis is set to what they actually deposited.
-- Safe to run more than once.

-- 1. Deposits may carry zero cost (fee units).
alter table public.ledger drop constraint if exists ledger_check;
alter table public.ledger add constraint ledger_check
  check ((kind = 'deposit' and units > 0 and capital >= 0) or (kind = 'withdrawal' and units < 0 and capital <= 0));

-- 2. Every reinvested fee, past and future, costs nothing.
create or replace function public._fee_units_cost_free() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.kind = 'deposit' and new.fee_for is not null then new.capital := 0; end if;
  return new;
end $$;
drop trigger if exists fee_units_cost_free on public.ledger;
create trigger fee_units_cost_free before insert or update on public.ledger
  for each row execute function public._fee_units_cost_free();
update public.ledger set capital = 0 where kind = 'deposit' and fee_for is not null and capital <> 0;

-- 3. The manager's cost basis: what they deposited themselves ($4.38). The difference is booked on their
--    earliest own deposit, so units and the share price do not change.
do $$
declare
  admin_id uuid := public._admin_member();
  target numeric := 4.38;
  have numeric;
  first_id uuid;
begin
  if admin_id is null then return; end if;
  select coalesce(sum(capital), 0) into have from public.ledger where member_id = admin_id;
  select id into first_id from public.ledger
    where member_id = admin_id and kind = 'deposit' and fee_for is null order by seq limit 1;
  if first_id is not null and abs(have - target) > 0.005 then
    update public.ledger set capital = greatest(capital + (target - have), 0),
                             note = coalesce(note || ' · ', '') || 'Cost basis set to $4.38'
      where id = first_id;
  end if;
end $$;

-- 4. Report: everyone still holding units, and the share price. Only Midas should be listed.
select m.name,
       round(sum(l.units), 4) as units,
       round(sum(l.capital), 2) as cost_basis,
       round(sum(l.units) * public.unit_price(), 2) as value_now,
       round(public.unit_price(), 4) as share_price,
       public.current_value() as fund_value
from public.ledger l join public.members m on m.id = l.member_id
group by m.name
having sum(l.units) > 0.0000001;
