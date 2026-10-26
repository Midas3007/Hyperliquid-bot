-- Starting data: the fund begins with only Midas (admin, no manager fee) and no deposits.
-- Midas records the first deposit from the admin page as a founder deposit (units at $1.00).
insert into public.settings (id, fund_name, fee_pct, total_slots, bot_wallet, started_at)
values (1, 'Midas × Kiyoshi Fund', 10, 15, '0x6cd37bc290c5c02ea92086d416a6a673bca581dc', current_date);

insert into public.members (name, is_admin, fee_exempt) values ('Midas', true, true);
