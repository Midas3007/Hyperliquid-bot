// Pure helper for gas.ts (kept apart so tests need no wallet key).
/** USDT (6 decimals) needed for `want` CELO, from what $1 buys (`celoPerUsdt`, in wei), plus 2% for price impact. */
export function usdtFor(want: bigint, celoPerUsdt: bigint) {
  return (want * 1_000_000n * 102n) / (celoPerUsdt * 100n) + 1n
}
/** What a top-up spends: enough for the target, but never more than the cap. */
export const topUpSpend = (needed: bigint, cap: bigint) => (needed < cap ? needed : cap)
