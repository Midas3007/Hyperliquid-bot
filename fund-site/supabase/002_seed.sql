-- Starting data from the "MIDAS x AXIØN FUND" spreadsheet (fund value $1,039.58).
-- Founders buy at 1.00; Dubem bought when the fund was worth $839.58.
insert into public.settings (id, fund_name, fee_pct, total_slots, bot_wallet, started_at)
values (1, 'Midas × Kiyoshi Fund', 10, 15, '0x6cd37bc290c5c02ea92086d416a6a673bca581dc', '2026-10-24');

insert into public.members (name, is_admin, fee_exempt) values ('Midas', true, true);
insert into public.members (name) values ('Stephen'), ('Chinedu'), ('DD'), ('Dubem');

select public._add_deposit((select id from public.members where name = 'Stephen'), 100,    '2026-10-22', null);
select public._add_deposit((select id from public.members where name = 'Chinedu'), 100,    '2026-10-22', null);
select public._add_deposit((select id from public.members where name = 'DD'),      206.43, '2026-10-22', null);
select public._add_deposit((select id from public.members where name = 'Midas'),   426.26, null,         null);
select public._add_deposit((select id from public.members where name = 'Dubem'),   200,    '2026-10-23', 839.58);

-- The deposits above each logged a snapshot at "now"; replace them with the spreadsheet's dated history.
delete from public.nav_snapshots;
insert into public.nav_snapshots (value, total_units, recorded_at, note) values
  (832.69,  832.69,                    '2026-10-22 12:00+00', 'Founders'),
  (839.58,  832.69,                    '2026-10-23 11:59+00', 'Fund value before Dubem'),
  (1039.58, (select public.total_units()), '2026-10-23 12:00+00', 'Deposit by Dubem');
