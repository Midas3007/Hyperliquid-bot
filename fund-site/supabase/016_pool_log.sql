-- Execution log for the dashboard: the pool wallet's most recent trades this season, from its own transactions
-- (pool_tx), with the route and the transaction hashes. Public chain data, so anyone may read it.
create or replace function public.pool_log(p_limit int default 100) returns jsonb
language sql stable security definer set search_path = '' as $$
  with w as (select lower(bot_wallet) as w, (extract(epoch from season_started_at) * 1000)::bigint as s from public.settings where id = 1),
  legs as (
    select x.t, x.usdt, x.block, x.tx_index, x.hash,
      case when x.to_addr = '0x5615cdab10dc425a742d643d949a7f474c01abc4' then (case when x.usdt < 0 and x.usdt > -1.01 then 'gas' else 'uni' end)
           when x.to_addr = '0xa9aa0a64769cbed4d3b1ceb4df01cde915c235b3' then 'tex' else 'other' end as venue,
      count(*) filter (where x.usdt > 0) over (order by x.block, x.tx_index rows between unbounded preceding and 1 preceding) as trade_no
    from public.pool_tx x, w
    where x.wallet = w.w and x.sent and x.usdt <> 0 and x.to_addr <> '0x48065fbbe25f71c9282ddf5e1cd6d6a887483d5e' and x.t >= w.s
  ), tr as (
    select max(t) filter (where usdt > 0) as t,
      sum(-usdt) filter (where usdt < 0 and venue <> 'gas') as size,
      sum(usdt) filter (where venue <> 'gas') as profit,
      string_agg(venue, '>' order by block, tx_index) filter (where venue <> 'gas') as path,
      jsonb_agg(hash order by block, tx_index) filter (where venue <> 'gas') as txs
    from legs group by trade_no
    having count(*) filter (where usdt > 0) = 1 and count(*) filter (where usdt < 0 and venue <> 'gas') > 0
  )
  select coalesce(jsonb_agg(jsonb_build_object('t', t, 'path', path, 'size', round(size, 2), 'profit', round(profit, 4), 'txs', txs) order by t desc), '[]'::jsonb)
  from (select * from tr order by t desc limit greatest(1, least(p_limit, 500))) z
$$;
revoke all on function public.pool_log(int) from public;
grant execute on function public.pool_log(int) to anon, authenticated, service_role;
