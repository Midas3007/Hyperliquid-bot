-- Careful mode (23 Nov 2026): a bot switch, on by default, flipped from Telegram (/careful_on, /careful_off).
-- When on, the bot needs a higher minimum edge, caps trades at $500 outside the 13:00-14:59 Lagos peak, re-checks
-- Textile's live price before a Uniswap-first trade, and trades only cNGN, USDC and wARS. Off = exactly the old behaviour. (Rules revised 28 Nov in the bot, kiyoshi/src/schedule.ts; no schema change.)
alter table public.bot_settings add column if not exists careful boolean not null default true;
alter table public.bot_settings add column if not exists careful_updated_at timestamptz;

create or replace function public.bot_settings() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('skip_reserved', skip_reserved, 'smart_sizing', smart_sizing, 'tight_slip', tight_slip,
    'careful', careful, 'updated_at', updated_at)
  from public.bot_settings where id = 1
$$;
