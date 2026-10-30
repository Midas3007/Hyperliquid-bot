// Keeper, run by the database every hour (pg_cron). Anyone may call it: it only ever does the right thing, at
// most once every 20 minutes. The Admin page's "Settle now" posts a message signed by the manager wallet.
//   1. Off-chain season: records the pool wallet's value (what sync-value does when someone opens the site).
//   2. On-chain fund (FundVault): posts the treasury's value; once a day after 20:00 UTC it settles the queued
//      deposits and redemptions, when the bot wallet holds enough USDT to pay the redemptions.
// It signs with its own keeper wallet (the vault's operator), which holds no money. The key is created here on the
// first run and kept encrypted in Supabase Vault; nobody needs to see it. Fund the keeper address with a little CELO.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'
import { createPublicClient, createWalletClient, defineChain, encodePacked, erc20Abi, formatUnits, http, isAddress, parseAbi } from 'npm:viem@2.21.0'
import { generatePrivateKey, privateKeyToAccount } from 'npm:viem@2.21.0/accounts'
import { celo } from 'npm:viem@2.21.0/chains'

const MIN_GAP_MS = 20 * 60_000
const SETTLE_HOUR_UTC = 20
const LEDGER_QUIET_MS = 5 * 60_000
const celoSepolia = defineChain({
  id: 11142220, name: 'Celo Sepolia', nativeCurrency: { name: 'CELO', symbol: 'CELO', decimals: 18 },
  rpcUrls: { default: { http: ['https://forno.celo-sepolia.celo-testnet.org'] } },
})
const NETS: Record<string, any> = {
  mainnet: { chain: celo, rpc: 'https://forno.celo.org' },
  testnet: { chain: celoSepolia, rpc: 'https://forno.celo-sepolia.celo-testnet.org' },
}
const mainnet = createPublicClient({ chain: celo, transport: http('https://forno.celo.org') })

// ---------- valuing the bot wallet on Celo mainnet (same rules as sync-value) ----------
const QUOTER = '0x82825d0554fA07f7FC52Ab63c961F330fdEFa8E8'
const USDT = '0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e'
const TOKENS = [
  { address: USDT, decimals: 6, stable: true },
  { address: '0xcebA9300f2b948710d2653dD7B07f33A8B32118C', decimals: 6, stable: true }, // USDC
  { address: '0xD76f5Faf6888e24D9F04Bf92a0c8B921FE4390e0', decimals: 18 }, // wBRL
  { address: '0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D', decimals: 18 }, // wARS
  { address: '0xF6829D7393dAe24509eb1E52eE8e572e2E271a4f', decimals: 6 }, // cNGN
  { address: '0x765DE816845861e75A25fCA122bb6898B8B1282a', decimals: 18 }, // USDm
  { address: '0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73', decimals: 18 }, // EURm
  { address: '0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787', decimals: 18 }, // BRLm
  { address: '0x456a3D042C0DbD3db53D5489e98dFb038553B0d0', decimals: 18 }, // KESm
  { address: '0x73F93dcc49cB8A239e2032663e9475dd5ef29A08', decimals: 18 }, // XOFm
  { address: '0x105d4A9306D2E55a71d2Eb95B81553AE1dC20d7B', decimals: 18 }, // PHPm
  { address: '0x8A567e2aE79CA692Bd748aB832081C45de4041eA', decimals: 18 }, // COPm
  { address: '0xfAeA5F3404bbA20D3cc2f8C4B0A888F55a3c7313', decimals: 18 }, // GHSm
] as const
const HUBS = ['0x765DE816845861e75A25fCA122bb6898B8B1282a', '0xcebA9300f2b948710d2653dD7B07f33A8B32118C'] as const
const quoterAbi = parseAbi([
  'function quoteExactInputSingle((address tokenIn,address tokenOut,uint256 amountIn,uint24 fee,uint160 sqrtPriceLimitX96)) returns (uint256 amountOut,uint160,uint32,uint256)',
  'function quoteExactInput(bytes path, uint256 amountIn) returns (uint256 amountOut, uint160[], uint32[], uint256)',
])
async function toUsdt(token: `0x${string}`, amount: bigint) {
  let best = 0n
  for (const fee of [100, 500, 3000, 10000]) {
    try {
      const { result } = await mainnet.simulateContract({ address: QUOTER, abi: quoterAbi, functionName: 'quoteExactInputSingle', args: [{ tokenIn: token, tokenOut: USDT, amountIn: amount, fee, sqrtPriceLimitX96: 0n }] })
      if (result[0] > best) best = result[0]
    } catch { /* no pool at this tier */ }
  }
  if (best === 0n) {
    for (const hub of HUBS) {
      if (hub.toLowerCase() === token.toLowerCase()) continue
      for (const f1 of [100, 500, 3000]) for (const f2 of [100, 500, 3000]) {
        try {
          const path = encodePacked(['address', 'uint24', 'address', 'uint24', 'address'], [token, f1, hub, f2, USDT])
          const { result } = await mainnet.simulateContract({ address: QUOTER, abi: quoterAbi, functionName: 'quoteExactInput', args: [path, amount] })
          if (result[0] > best) best = result[0]
        } catch { /* no such path */ }
      }
    }
  }
  return best
}
/** Value of a mainnet wallet in USDT units (6 decimals). */
async function walletValue(wallet: `0x${string}`) {
  let value = 0n
  for (const t of TOKENS) {
    const raw = await mainnet.readContract({ address: t.address as `0x${string}`, abi: erc20Abi, functionName: 'balanceOf', args: [wallet] })
    if (raw === 0n) continue
    value += 'stable' in t ? (t.decimals === 6 ? raw : raw / 10n ** BigInt(t.decimals - 6)) : await toUsdt(t.address as `0x${string}`, raw)
  }
  return value
}

const vaultAbi = parseAbi([
  'function nav() view returns (uint256)', 'function totalSupply() view returns (uint256)', 'function paused() view returns (bool)',
  'function operator() view returns (address)', 'function manager() view returns (address)', 'function owner() view returns (address)', 'function treasury() view returns (address)', 'function totalPendingDeposits() view returns (uint256)',
  'function queueLengths() view returns (uint256, uint256)', 'function balanceOf(address) view returns (uint256)', 'function sharePrice() view returns (uint256)',
  'function reportValue(uint256 newNav)', 'function settle(uint256 newNav)',
])
const fmt = (v: bigint) => '$' + Number(formatUnits(v, 6)).toFixed(2)

Deno.serve(async (req) => {
  const force = new URL(req.url).searchParams.get('force') === '1'
  const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type', 'Access-Control-Allow-Methods': 'POST, OPTIONS' }
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })
  const reply = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json', ...cors } })
  const { data: cfg } = await db.from('vault_config').select('*').eq('id', 1).single()
  if (!cfg) return reply({ error: 'vault_config missing' })
  // "Settle now" from the site's Admin page: a message signed by the vault's manager or owner, at most 10 minutes old
  const ask = req.method === 'POST' ? await req.json().catch(() => null) : null
  if (ask?.settle) {
    try {
      const { message, signature } = ask.settle
      const m = /^Midas × Kiyoshi Fund: settle now\nVault: (0x[0-9a-fA-F]{40})\nTime: (\S+)$/.exec(String(message))
      if (!m || m[1].toLowerCase() !== String(cfg.vault_address).toLowerCase()) throw new Error('the request is not for this fund')
      if (!(Math.abs(Date.now() - Date.parse(m[2])) < 10 * 60_000)) throw new Error('the request is too old; try again')
      const net = NETS[cfg.network]
      const pub = createPublicClient({ chain: net.chain, transport: http(net.rpc) })
      const vault = cfg.vault_address as `0x${string}`
      const [manager, owner] = await Promise.all(['manager', 'owner'].map((f) => pub.readContract({ address: vault, abi: vaultAbi, functionName: f as any }) as Promise<`0x${string}`>))
      let by = ''
      for (const a of [manager, owner]) if (!by && await pub.verifyMessage({ address: a, message: String(message), signature }).catch(() => false)) by = a
      if (!by) throw new Error('only the fund manager or the bot wallet can do this')
      await db.from('vault_config').update({ settle_now: true }).eq('id', 1)
      cfg.settle_now = true
    } catch (e) { return reply({ error: `settle now: ${(e as Error).message.split('\n')[0]}` }) }
  }
  if (!force && !cfg.settle_now && cfg.last_run_at && Date.now() - Date.parse(cfg.last_run_at) < MIN_GAP_MS) return reply({ skipped: 'ran recently' })
  await db.from('vault_config').update({ last_run_at: new Date().toISOString() }).eq('id', 1)
  const notes: string[] = []

  // 1. Off-chain season value (the members' ledger in Supabase)
  try {
    const { data: s } = await db.from('settings').select('bot_wallet').eq('id', 1).single()
    const { data: lastEntry } = await db.from('ledger').select('created_at').order('seq', { ascending: false }).limit(1).maybeSingle()
    if (s?.bot_wallet && isAddress(s.bot_wallet) && !(lastEntry && Date.now() - Date.parse(lastEntry.created_at) < LEDGER_QUIET_MS)) {
      const v = Math.round(Number(formatUnits(await walletValue(s.bot_wallet as `0x${string}`), 6)) * 100) / 100
      const { data: last } = await db.from('nav_snapshots').select('value').order('recorded_at', { ascending: false }).order('id', { ascending: false }).limit(1).maybeSingle()
      if (!last || Math.abs(Number(last.value) - v) >= 0.005) {
        const { data: units } = await db.from('ledger').select('units')
        const total = (units ?? []).reduce((a: number, r: any) => a + Number(r.units), 0)
        await db.from('nav_snapshots').insert({ value: v, total_units: total, note: 'Synced from pool wallet (keeper)' })
        notes.push(`season value $${v.toFixed(2)}`)
      }
    }
  } catch (e) { notes.push(`season sync failed: ${(e as Error).message.split('\n')[0]}`) }

  // 2. The keeper wallet: created once, kept in Supabase Vault
  let { data: key } = await db.rpc('_keeper_key')
  if (!key) {
    const k = generatePrivateKey()
    const { error } = await db.rpc('_keeper_set_key', { p_key: k, p_address: privateKeyToAccount(k).address })
    if (error) return reply({ error: `could not store the keeper key: ${error.message}` })
    key = k
    notes.push(`keeper wallet created: ${privateKeyToAccount(k).address}`)
  }
  const account = privateKeyToAccount(key as `0x${string}`)

  // 3. The on-chain fund
  const net = NETS[cfg.network]
  if (!cfg.vault_address || !net || !isAddress(cfg.vault_address)) {
    await db.from('vault_config').update({ last_result: notes.join(' · ') || 'no vault configured' }).eq('id', 1)
    return reply({ notes, vault: 'none' })
  }
  const vault = cfg.vault_address as `0x${string}`
  const pub = createPublicClient({ chain: net.chain, transport: http(net.rpc) })
  const wallet = createWalletClient({ account, chain: net.chain, transport: http(net.rpc) })
  const read = (fn: string, args: unknown[] = []) => pub.readContract({ address: vault, abi: vaultAbi, functionName: fn as any, args: args as any }) as Promise<any>
  const write = async (fn: 'reportValue' | 'settle', value: bigint) => {
    const { request } = await pub.simulateContract({ account, address: vault, abi: vaultAbi, functionName: fn, args: [value] })
    const hash = await wallet.writeContract(request)
    const r = await pub.waitForTransactionReceipt({ hash, timeout: 60_000 })
    if (r.status !== 'success') throw new Error(`${fn} failed: ${hash}`)
    // Record the result for the chart. The transaction has succeeded by now, so nothing below may throw.
    // Read at the receipt's block (a load-balanced RPC can answer from the block before); the public node
    // sometimes refuses reads at a given block, so retry, then fall back to the latest block.
    try {
      const read3 = (blockNumber?: bigint) => Promise.all(['nav', 'totalSupply', 'sharePrice'].map((f) =>
        pub.readContract({ address: vault, abi: vaultAbi, functionName: f as any, ...(blockNumber ? { blockNumber } : {}) }) as Promise<bigint>))
      let vals: bigint[] | null = null
      for (let i = 0; i < 3 && !vals; i++) {
        vals = await read3(r.blockNumber).catch(() => null)
        if (!vals) await new Promise((ok) => setTimeout(ok, 2000))
      }
      const [nav, supply, price] = vals ?? (await read3())
      await db.from('vault_history').insert({ network: cfg.network, vault_address: vault, kind: fn === 'settle' ? 'settle' : 'report', nav: Number(nav) / 1e6, supply: Number(supply) / 1e18, price: Number(price) / 1e6, tx: hash })
    } catch (e) { notes.push(`history not recorded: ${String((e as any)?.shortMessage || (e as Error).message).split('\n')[0]}`) }
    return hash
  }
  try {
    if (await read('paused')) throw new Error('paused')
    if ((await read('operator')).toLowerCase() !== account.address.toLowerCase()) throw new Error(`this keeper (${account.address}) is not the vault's operator`)
    if ((await pub.getBalance({ address: account.address })) === 0n) throw new Error(`the keeper wallet ${account.address} needs a little CELO for gas`)
    const treasury = await read('treasury')
    const usdt = cfg.usdt_address as `0x${string}`
    const value: bigint = cfg.network === 'mainnet'
      ? await walletValue(treasury)
      : await pub.readContract({ address: usdt, abi: erc20Abi, functionName: 'balanceOf', args: [treasury] })
    const [nav, q, supply] = await Promise.all([read('nav'), read('queueLengths'), read('totalSupply')])
    const now = new Date(), today = now.toISOString().slice(0, 10)
    // settle_now: a one-time early settlement the admin asks for (e.g. to test); cleared once used
    const due = (now.getUTCHours() >= SETTLE_HOUR_UTC && cfg.last_settle_day !== today) || cfg.settle_now === true
    if (due && (q[0] > 0n || q[1] > 0n)) {
      const [pendingDeposits, escrow] = await Promise.all([read('totalPendingDeposits'), read('balanceOf', [vault])])
      const payouts = supply > 0n ? escrow * value / supply : 0n
      const need = payouts > pendingDeposits ? payouts - pendingDeposits : 0n
      const cash = await pub.readContract({ address: usdt, abi: erc20Abi, functionName: 'balanceOf', args: [treasury] })
      if (cash < need) notes.push(`settlement waiting: the bot wallet holds ${fmt(cash)} USDT, payouts need about ${fmt(need)}`)
      else {
        await write('settle', value)
        await db.from('vault_config').update(cfg.settle_now ? { settle_now: false } : { last_settle_day: today }).eq('id', 1)
        notes.push(`settled ${q[0]} deposit(s), ${q[1]} redemption(s) at ${fmt(value)}`)
      }
    } else {
      if (due) await db.from('vault_config').update(cfg.settle_now ? { settle_now: false } : { last_settle_day: today }).eq('id', 1)
      const diff = value > nav ? value - nav : nav - value
      if (supply > 0n && diff >= 10_000n) { // at least one cent
        await write('reportValue', value)
        notes.push(`value ${fmt(nav)} -> ${fmt(value)}`)
      } else notes.push(`value ${fmt(value)}`)
    }
  } catch (e) {
    const m = (e as any)?.shortMessage || (e as Error).message
    notes.push(/ValueJump/.test(m) ? 'the value moved more than the daily limit allows; check the bot wallet' : `vault: ${String(m).split('\n')[0]}`)
  }
  await db.from('vault_config').update({ last_result: notes.join(' · ') }).eq('id', 1)
  return reply({ notes })
})
