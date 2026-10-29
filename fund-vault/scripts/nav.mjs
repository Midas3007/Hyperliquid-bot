// The fund's value: everything the bot wallet holds, priced in USDT (same rules as the members' site:
// USDT and USDC at $1, local-currency and Mento tokens at their live Uniswap v3 price, CELO for gas ignored).
// Money queued in the vault is not counted: it is not the fund's until it settles.
import { encodePacked, erc20Abi, formatUnits, parseAbi } from 'viem'

const QUOTER = '0x82825d0554fA07f7FC52Ab63c961F330fdEFa8E8'
const USDT = '0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e'
const TOKENS = [
  { symbol: 'USDT', address: USDT, decimals: 6, stable: true },
  { symbol: 'USDC', address: '0xcebA9300f2b948710d2653dD7B07f33A8B32118C', decimals: 6, stable: true },
  { symbol: 'wBRL', address: '0xD76f5Faf6888e24D9F04Bf92a0c8B921FE4390e0', decimals: 18 },
  { symbol: 'wARS', address: '0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D', decimals: 18 },
  { symbol: 'cNGN', address: '0xF6829D7393dAe24509eb1E52eE8e572e2E271a4f', decimals: 6 },
  { symbol: 'USDm', address: '0x765DE816845861e75A25fCA122bb6898B8B1282a', decimals: 18 },
  { symbol: 'EURm', address: '0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73', decimals: 18 },
  { symbol: 'BRLm', address: '0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787', decimals: 18 },
  { symbol: 'KESm', address: '0x456a3D042C0DbD3db53D5489e98dFb038553B0d0', decimals: 18 },
  { symbol: 'XOFm', address: '0x73F93dcc49cB8A239e2032663e9475dd5ef29A08', decimals: 18 },
  { symbol: 'PHPm', address: '0x105d4A9306D2E55a71d2Eb95B81553AE1dC20d7B', decimals: 18 },
  { symbol: 'COPm', address: '0x8A567e2aE79CA692Bd748aB832081C45de4041eA', decimals: 18 },
  { symbol: 'GHSm', address: '0xfAeA5F3404bbA20D3cc2f8C4B0A888F55a3c7313', decimals: 18 },
]
const HUBS = ['0x765DE816845861e75A25fCA122bb6898B8B1282a', '0xcebA9300f2b948710d2653dD7B07f33A8B32118C']
const quoterAbi = parseAbi([
  'function quoteExactInputSingle((address tokenIn,address tokenOut,uint256 amountIn,uint24 fee,uint160 sqrtPriceLimitX96)) returns (uint256 amountOut,uint160,uint32,uint256)',
  'function quoteExactInput(bytes path, uint256 amountIn) returns (uint256 amountOut, uint160[], uint32[], uint256)',
])

async function toUsdt(pub, token, amount) {
  let best = 0n
  for (const fee of [100, 500, 3000, 10000]) {
    try {
      const { result } = await pub.simulateContract({ address: QUOTER, abi: quoterAbi, functionName: 'quoteExactInputSingle', args: [{ tokenIn: token, tokenOut: USDT, amountIn: amount, fee, sqrtPriceLimitX96: 0n }] })
      if (result[0] > best) best = result[0]
    } catch { /* no pool at this tier */ }
  }
  if (best === 0n) {
    for (const hub of HUBS) {
      if (hub.toLowerCase() === token.toLowerCase()) continue
      for (const f1 of [100, 500, 3000]) for (const f2 of [100, 500, 3000]) {
        try {
          const path = encodePacked(['address', 'uint24', 'address', 'uint24', 'address'], [token, f1, hub, f2, USDT])
          const { result } = await pub.simulateContract({ address: QUOTER, abi: quoterAbi, functionName: 'quoteExactInput', args: [path, amount] })
          if (result[0] > best) best = result[0]
        } catch { /* no such path */ }
      }
    }
  }
  return best
}

/** The bot wallet's value in USDT units (6 decimals), and what it is made of. */
export async function walletValue(net, wallet, dep) {
  if (net.name !== 'mainnet') { // test funds hold only test USDT
    const raw = await net.pub.readContract({ address: dep.usdt, abi: erc20Abi, functionName: 'balanceOf', args: [wallet] })
    return { value: raw, holdings: [{ symbol: 'tUSDT', amount: Number(formatUnits(raw, 6)), usd: Number(formatUnits(raw, 6)) }] }
  }
  let value = 0n
  const holdings = []
  for (const t of TOKENS) {
    const raw = await net.pub.readContract({ address: t.address, abi: erc20Abi, functionName: 'balanceOf', args: [wallet] })
    if (raw === 0n) continue
    const v = t.stable ? (t.decimals === 6 ? raw : raw / 10n ** BigInt(t.decimals - 6)) : await toUsdt(net.pub, t.address, raw)
    holdings.push({ symbol: t.symbol, amount: Number(formatUnits(raw, t.decimals)), usd: Number(formatUnits(v, 6)) })
    value += v
  }
  return { value, holdings }
}
