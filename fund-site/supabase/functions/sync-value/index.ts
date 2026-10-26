// Sets the fund value from the pool wallet's live on-chain balance.
// Any signed-in member can trigger it: the value comes from the chain, never from the caller.
// POST { peek: true } only reads the live value (for the admin deposit and withdrawal forms) and saves nothing.
// Counts USDT + USDC at $1, and every other token the bot can hold (wBRL, wARS, cNGN and the Mento stablecoins)
// at its live Uniswap v3 price in USDT, directly or through USDm/USDC. CELO (gas) is ignored.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'
import { createPublicClient, encodePacked, erc20Abi, formatUnits, http, isAddress, parseAbi } from 'npm:viem@2.21.0'
import { celo } from 'npm:viem@2.21.0/chains'

const MIN_GAP_MS = 2 * 60_000          // at most one sync every 2 minutes
const LEDGER_QUIET_MS = 5 * 60_000     // pause after a deposit/withdrawal so the money can land or leave
const QUOTER = '0x82825d0554fA07f7FC52Ab63c961F330fdEFa8E8'
const USDT = '0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e'
const TOKENS = [
  { symbol: 'USDT', address: USDT, decimals: 6, stable: true },
  { symbol: 'USDC', address: '0xcebA9300f2b948710d2653dD7B07f33A8B32118C', decimals: 6, stable: true },
  { symbol: 'wBRL', address: '0xD76f5Faf6888e24D9F04Bf92a0c8B921FE4390e0', decimals: 18, stable: false },
  { symbol: 'wARS', address: '0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D', decimals: 18, stable: false },
  { symbol: 'cNGN', address: '0xF6829D7393dAe24509eb1E52eE8e572e2E271a4f', decimals: 6, stable: false },
  { symbol: 'USDm', address: '0x765DE816845861e75A25fCA122bb6898B8B1282a', decimals: 18, stable: false },
  { symbol: 'EURm', address: '0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73', decimals: 18, stable: false },
  { symbol: 'BRLm', address: '0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787', decimals: 18, stable: false },
  { symbol: 'KESm', address: '0x456a3D042C0DbD3db53D5489e98dFb038553B0d0', decimals: 18, stable: false },
  { symbol: 'XOFm', address: '0x73F93dcc49cB8A239e2032663e9475dd5ef29A08', decimals: 18, stable: false },
  { symbol: 'PHPm', address: '0x105d4A9306D2E55a71d2Eb95B81553AE1dC20d7B', decimals: 18, stable: false },
  { symbol: 'COPm', address: '0x8A567e2aE79CA692Bd748aB832081C45de4041eA', decimals: 18, stable: false },
  { symbol: 'GHSm', address: '0xfAeA5F3404bbA20D3cc2f8C4B0A888F55a3c7313', decimals: 18, stable: false },
] as const
const HUBS = ['0x765DE816845861e75A25fCA122bb6898B8B1282a', '0xcebA9300f2b948710d2653dD7B07f33A8B32118C'] as const // USDm, USDC
const quoterAbi = parseAbi([
  'function quoteExactInputSingle((address tokenIn,address tokenOut,uint256 amountIn,uint24 fee,uint160 sqrtPriceLimitX96)) returns (uint256 amountOut,uint160,uint32,uint256)',
  'function quoteExactInput(bytes path, uint256 amountIn) returns (uint256 amountOut, uint160[], uint32[], uint256)',
])

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
}
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } })

const chain = createPublicClient({ chain: celo, transport: http('https://forno.celo.org') })

// Best USDT output across Uniswap v3 fee tiers, or 0 if no pool can take it.
async function toUsdt(token: `0x${string}`, amount: bigint) {
  let best = 0n
  for (const fee of [100, 500, 3000, 10000]) {
    try {
      const { result } = await chain.simulateContract({
        address: QUOTER, abi: quoterAbi, functionName: 'quoteExactInputSingle',
        args: [{ tokenIn: token, tokenOut: USDT, amountIn: amount, fee, sqrtPriceLimitX96: 0n }],
      })
      if (result[0] > best) best = result[0]
    } catch { /* no pool at this tier */ }
  }
  // No direct pool: try one hop through USDm or USDC.
  if (best === 0n) {
    for (const hub of HUBS) {
      if (hub.toLowerCase() === token.toLowerCase()) continue
      for (const f1 of [100, 500, 3000]) for (const f2 of [100, 500, 3000]) {
        try {
          const path = encodePacked(['address', 'uint24', 'address', 'uint24', 'address'], [token, f1, hub, f2, USDT])
          const { result } = await chain.simulateContract({ address: QUOTER, abi: quoterAbi, functionName: 'quoteExactInput', args: [path, amount] })
          if (result[0] > best) best = result[0]
        } catch { /* no such path */ }
      }
    }
  }
  return Number(formatUnits(best, 6))
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })

  const url = Deno.env.get('SUPABASE_URL')!
  const caller = createClient(url, Deno.env.get('SUPABASE_ANON_KEY')!, {
    global: { headers: { Authorization: req.headers.get('Authorization') ?? '' } },
  })
  const { data: memberId } = await caller.rpc('my_member_id')
  if (!memberId) return reply(403, { error: 'Members only' })

  const db = createClient(url, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const { data: s } = await db.from('settings').select('bot_wallet').eq('id', 1).single()
  const wallet = s?.bot_wallet
  if (!wallet || !isAddress(wallet)) return reply(200, { skipped: 'no pool wallet set' })

  let peek = false
  try { peek = (await req.json())?.peek === true } catch { /* no body */ }

  const now = Date.now()
  const { data: last } = await db.from('nav_snapshots').select('value, recorded_at').order('recorded_at', { ascending: false }).order('id', { ascending: false }).limit(1).maybeSingle()
  if (!peek) {
    const { data: lastEntry } = await db.from('ledger').select('created_at').order('seq', { ascending: false }).limit(1).maybeSingle()
    if (lastEntry && now - Date.parse(lastEntry.created_at) < LEDGER_QUIET_MS) {
      return reply(200, { skipped: 'recent deposit or withdrawal', resumes_at: new Date(Date.parse(lastEntry.created_at) + LEDGER_QUIET_MS).toISOString() })
    }
    if (last && now - Date.parse(last.recorded_at) < MIN_GAP_MS) return reply(200, { skipped: 'synced recently' })
  }

  const holdings = []
  let value = 0
  for (const t of TOKENS) {
    const raw = await chain.readContract({ address: t.address, abi: erc20Abi, functionName: 'balanceOf', args: [wallet] })
    if (raw === 0n) continue
    const amount = Number(formatUnits(raw, t.decimals))
    const usd = t.stable ? amount : await toUsdt(t.address, raw)
    holdings.push({ symbol: t.symbol, amount, usd })
    value += usd
  }
  value = Math.round(value * 100) / 100
  if (peek) return reply(200, { value, holdings })

  if (last && Math.abs(Number(last.value) - value) < 0.005) return reply(200, { unchanged: true, value, holdings })

  const { data: units } = await db.from('ledger').select('units')
  const totalUnits = (units ?? []).reduce((sum, r) => sum + Number(r.units), 0)
  const { error } = await db.from('nav_snapshots').insert({ value, total_units: totalUnits, note: 'Synced from pool wallet' })
  if (error) return reply(500, { error: error.message })
  return reply(200, { recorded: true, value, holdings })
})
