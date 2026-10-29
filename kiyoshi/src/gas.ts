// Midas's addition: keep the bot wallet's gas topped up. When its CELO falls below GAS_TOPUP_BELOW (0.5), it buys
// GAS_TOPUP_CELO (5) with USDT on Uniswap v3: directly, or through USDm when that is cheaper. Every top-up spends at
// most GAS_TOPUP_MAX_USD ($0.50) of USDT (if 5 CELO costs more, it buys what $0.50 gets), waits at least 6 hours after the previous one, and is skipped while the
// desk holds leftover inventory or the fund is settling. The USDT spent leaves the fund's value, so its cost is
// shared by every shareholder in proportion to their shares. The transaction carries the attribution tag like
// every other one (sendTaggedReceipt).
import { encodeFunctionData, encodePacked, erc20Abi, formatEther, formatUnits, parseAbi, parseEventLogs, parseEther, parseUnits, type Address, type Hex } from 'viem'
import { account, pub, sendTaggedReceipt } from './wallet.js'
import { ensureAllowance } from './swap.js'
import { TOKENS, UNI } from './config.js'
import { topUpSpend, usdtFor } from './gas-math.js'

const CELO: Address = '0x471EcE3750Da237f93B8E339c536989b8978a438' // CELO's ERC-20 interface: the same balance as native gas
const USDM: Address = '0x765DE816845861e75A25fCA122bb6898B8B1282a'
const BELOW = parseEther(process.env.GAS_TOPUP_BELOW ?? '0.5')
const BUY = parseEther(process.env.GAS_TOPUP_CELO ?? '5')
const MAX_USD = parseUnits(process.env.GAS_TOPUP_MAX_USD ?? '0.5', 6)
const EVERY_MS = 6 * 3_600_000
const CHECK_MS = 10 * 60_000
const SLIPPAGE_BPS = 100n // 1%

const quoterAbi = parseAbi(['function quoteExactInput(bytes path, uint256 amountIn) returns (uint256 amountOut, uint160[], uint32[], uint256)'])
const routerAbi = parseAbi([
  'function exactInput((bytes path,address recipient,uint256 amountIn,uint256 amountOutMinimum)) payable returns (uint256 amountOut)',
  'function factory() view returns (address)',
])

export type TopUp = { at: string; usdt: number; celo: number; tx: Hex }
export const gasState: { celo: number | null; lastTopUp: TopUp | null; lastError: string | null } = { celo: null, lastTopUp: null, lastError: null }
let checkedAt = 0
let toppedAt = 0

const paths = (): Hex[] => {
  const out: Hex[] = []
  for (const fee of [100, 500, 3000, 10000]) out.push(encodePacked(['address', 'uint24', 'address'], [TOKENS.USDT.address, fee, CELO]))
  for (const f1 of [100, 500]) for (const f2 of [500, 3000, 10000]) out.push(encodePacked(['address', 'uint24', 'address', 'uint24', 'address'], [TOKENS.USDT.address, f1, USDM, f2, CELO]))
  return out
}
async function bestPath(amountIn: bigint) {
  let best: { path: Hex; out: bigint } | null = null
  for (const path of paths()) {
    try {
      const { result } = await pub.simulateContract({ address: UNI.QUOTER, abi: quoterAbi, functionName: 'quoteExactInput', args: [path, amountIn] })
      if (!best || result[0] > best.out) best = { path, out: result[0] }
    } catch { /* no pool on this route */ }
  }
  return best
}

/** Call once per cycle. Returns a log line when it did something, otherwise null. Never throws. */
export async function gasTopUp(opts: { canTrade: boolean }): Promise<string | null> {
  if (Date.now() - checkedAt < CHECK_MS) return null
  checkedAt = Date.now()
  try {
    const bal = await pub.getBalance({ address: account.address })
    gasState.celo = Number(formatEther(bal))
    if (bal >= BELOW || !opts.canTrade || Date.now() - toppedAt < EVERY_MS) return null
    const unit = await bestPath(1_000_000n) // $1
    if (!unit || unit.out === 0n) throw new Error('no Uniswap route from USDT to CELO')
    const amountIn = topUpSpend(usdtFor(BUY, unit.out), MAX_USD)
    const cash = await pub.readContract({ address: TOKENS.USDT.address, abi: erc20Abi, functionName: 'balanceOf', args: [account.address] })
    if (cash < amountIn) throw new Error(`not enough USDT for gas ($${formatUnits(cash, 6)})`)
    const route = await bestPath(amountIn)
    if (!route) throw new Error('no Uniswap route from USDT to CELO')
    const f = await pub.readContract({ address: UNI.ROUTER, abi: routerAbi, functionName: 'factory' })
    if (f.toLowerCase() !== UNI.FACTORY.toLowerCase()) throw new Error('router factory mismatch, refusing to swap')
    await ensureAllowance(TOKENS.USDT.address, UNI.ROUTER, amountIn)
    toppedAt = Date.now()
    const receipt = await sendTaggedReceipt({
      to: UNI.ROUTER,
      data: encodeFunctionData({ abi: routerAbi, functionName: 'exactInput', args: [{ path: route.path, recipient: account.address, amountIn, amountOutMinimum: (route.out * (10_000n - SLIPPAGE_BPS)) / 10_000n }] }),
    })
    const got = parseEventLogs({ abi: erc20Abi, logs: receipt.logs, eventName: 'Transfer' })
      .filter((l) => l.address.toLowerCase() === CELO.toLowerCase() && l.args.to.toLowerCase() === account.address.toLowerCase())
      .reduce((s, l) => s + l.args.value, 0n)
    gasState.lastTopUp = { at: new Date().toISOString(), usdt: Number(formatUnits(amountIn, 6)), celo: Number(formatEther(got)), tx: receipt.transactionHash }
    gasState.celo = Number(formatEther(await pub.getBalance({ address: account.address })))
    gasState.lastError = null
    return `gas: bought ${gasState.lastTopUp.celo.toFixed(3)} CELO for $${gasState.lastTopUp.usdt.toFixed(2)} USDT (${receipt.transactionHash})`
  } catch (e: any) {
    gasState.lastError = String(e.shortMessage ?? e.message).split('\n')[0]
    return `gas: top-up not done: ${gasState.lastError}`
  }
}
