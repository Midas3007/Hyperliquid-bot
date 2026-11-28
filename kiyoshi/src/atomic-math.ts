// Pure rules for atomic trading (atomic.ts), unit tested in test/atomic.test.ts. Amounts are USDT micro-units (6 dp).

export const MIN_TRADE = 20_000000n // below $20 a trade is not worth a Textile quote
export const SKIM_SLACK = 25_000000n // profit above the float target goes back to the bot wallet in $25+ steps
export const WALLET_RESERVE_SHARE = 50n // a top-up leaves the bot wallet at least half its USDT for its own trades

/**
 * Sizes to try (point 6 of the plan). Uniswap's price moves about 0.58 bps per $100 traded in these pools, so a gap
 * of g bps pays most at roughly g x $86 (5 bps: ~$430; 10 bps: ~$860). Instead of guessing g, try a spread of sizes
 * up to the float and keep the one with the most dollars of profit.
 */
export function sizesFor(float: bigint, steps = [0.25, 0.5, 0.75, 0.98]): bigint[] {
  const out = new Set<bigint>()
  for (const f of steps) {
    const s = (float * BigInt(Math.round(f * 1000))) / 1000n
    const rounded = (s / 1_000000n) * 1_000000n // whole dollars
    if (rounded >= MIN_TRADE) out.add(rounded)
  }
  return [...out].sort((a, b) => (a < b ? -1 : 1))
}

/**
 * Least expected profit worth an attempt. The leg risk is gone (a losing trade reverts), so only fees matter: Textile's
 * 2 bps fee is already inside its price, and a try costs about $0.001 of gas. Default: 1 bp of the size, at least $0.02.
 */
export const entryMin = (size: bigint, bps = 1, floor = 20_000n) => {
  const b = (size * BigInt(Math.round(bps * 100))) / 1_000_000n
  return b > floor ? b : floor
}

/** The contract's minimum profit at execution: a fixed floor below the expected profit, never zero. */
export const execMin = (size: bigint, bps = 0.5) => {
  const b = (size * BigInt(Math.round(bps * 100))) / 1_000_000n
  return b > 1n ? b : 1n
}

export type FloatAction = { kind: 'top-up'; amount: bigint } | { kind: 'skim'; amount: bigint } | null

/** Keep the contract's USDT near the target: top it up from the bot wallet, or send profit above it back. */
export function floatAction(contractUsdt: bigint, target: bigint, walletUsdt: bigint): FloatAction {
  if (contractUsdt > target + SKIM_SLACK) return { kind: 'skim', amount: contractUsdt - target }
  if (contractUsdt * 10n >= target * 9n) return null // within 10% below the target: leave it
  const need = target - contractUsdt
  const spare = (walletUsdt * (100n - WALLET_RESERVE_SHARE)) / 100n
  const amount = need < spare ? need : spare
  return amount >= MIN_TRADE ? { kind: 'top-up', amount } : null
}
