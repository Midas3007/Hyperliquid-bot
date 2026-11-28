-- Atomic trading (29 Nov 2026): the KiyoshiArb contract (kiyoshi/contracts) does Textile-first trades in one transaction.
--   settings.arb_contract: the contract; the keeper (vault-keeper) and the site (sync-value) count its balances in the
--     fund's value, since it holds part of the fund's USDT (the float).
--   bot_settings.atomic: Telegram /atomic_on|off (off by default). Off returns the float to the bot wallet.
--   bot_settings.arb_float_usd: the float's target (Telegram /float <usd>, $0-5000).
alter table public.settings add column if not exists arb_contract text;
update public.settings set arb_contract = '0x16c790305ecf31ed639d9390a37f4b1386fd8cfb' where id = 1;
alter table public.bot_settings add column if not exists atomic boolean not null default false;
alter table public.bot_settings add column if not exists atomic_updated_at timestamptz;
alter table public.bot_settings add column if not exists arb_float_usd numeric not null default 300
  check (arb_float_usd >= 0 and arb_float_usd <= 5000);

create or replace function public.bot_settings() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('skip_reserved', skip_reserved, 'smart_sizing', smart_sizing, 'tight_slip', tight_slip,
    'careful', careful, 'atomic', atomic, 'arb_float_usd', arb_float_usd, 'updated_at', updated_at)
  from public.bot_settings where id = 1
$$;
