// The fund treasury (014_treasury.sql), as the keeper and sync-value use it. No Deno-only code: the same file runs
// under Node for testing (fund-site/supabase/test/treasury.test.mjs).
//
// What it does for them, in one sentence: the value posted for the fund is the wallet's value MINUS the treasury pot,
// and when the pot cannot be read, or the pot and the wallet disagree for a moment, nothing is posted.
// The rules (each one exists because breaking it would post a wrong share price):
//  - Treasury off, not installed yet (014 not run), or a wallet that is not the pot's wallet: behave exactly as before.
//  - Anything else that goes wrong while the treasury is on: post NOTHING this run. A value with the pot still inside it
//    would raise every share's price by the pot, and members could redeem at it. The keeper tries again an hour later.
//  - The settlement carve happens before the value is read, and the settlement waits if the carve could not run.

export const MAX_LAG_BLOCKS = 45 // the stored trade history may be this many blocks (about 45 seconds) behind the chain

export type TreasuryStatus = {
  active: boolean; wallet?: string | null; balance?: number | string; hold?: boolean; hold_reason?: string | null; lag_blocks?: number | null
}
export type Posted =
  | { kind: 'plain'; micro: bigint }                       // no treasury applies: the wallet value, as before
  | { kind: 'value'; micro: bigint; pot: bigint; carvePending?: boolean } // wallet value minus the pot
  | { kind: 'skip'; why: string }                          // post nothing this run

const micro = (x: number | string) => BigInt(Math.round(Number(x) * 1e6))

/** The database has no treasury functions yet (014 not run): not an error, the keeper behaves as before. */
export const isNotInstalled = (e: any) => e?.code === 'PGRST202' || e?.code === '42883' || /could not find the function/i.test(String(e?.message ?? ''))

/** Pure: what to post for a wallet worth `walletMicro` (USDT, 6 decimals) given the treasury's status. */
export function postedValue(walletMicro: bigint, st: TreasuryStatus | null, forWallet: string | null): Posted {
  if (!st) return { kind: 'skip', why: 'the treasury status could not be read' }
  if (!st.active) return { kind: 'plain', micro: walletMicro }
  if (!forWallet || !st.wallet || st.wallet.toLowerCase() !== forWallet.toLowerCase()) return { kind: 'plain', micro: walletMicro }
  if (st.hold) return { kind: 'skip', why: `waiting: ${st.hold_reason ?? 'a treasury movement is not matched yet'}` }
  if (st.lag_blocks == null || !(st.lag_blocks <= MAX_LAG_BLOCKS)) return { kind: 'skip', why: `waiting: the trade history is ${st.lag_blocks == null ? 'not' : st.lag_blocks + ' blocks'} up to date with the chain` }
  const pot = micro(st.balance ?? 0)
  if (pot < 0n || walletMicro - pot <= 0n) return { kind: 'skip', why: 'the treasury pot is not smaller than the wallet value; check the treasury' }
  return { kind: 'value', micro: walletMicro - pot, pot }
}

type Db = { rpc: (name: string, args?: Record<string, unknown>) => PromiseLike<{ data: any; error: any }> }
type Opts = {
  due: boolean                       // this run is the settlement run (carve first)
  forWallet: string                  // the wallet this value is for (the pot only applies to its own wallet)
  head: () => Promise<bigint>        // the chain's latest block number
  wallet: () => Promise<bigint>      // the wallet's value in USDT micro-units, as the keeper counts it
  refresh: () => Promise<unknown>    // bring the stored trade history up to the head of the chain (best effort)
}

/**
 * The value to post. Order matters: history refreshed, gas and withdrawals synced, the carve (settlement run only), THEN the
 * wallet is read, then the pot. Anything that happened on the chain before the wallet was read is in the pot by then, or the
 * run reports that the history is behind and posts nothing.
 */
export async function treasuryValue(db: Db, o: Opts): Promise<Posted & { notes?: string[] }> {
  const first = await db.rpc('treasury_status')
  if (first.error) {
    if (isNotInstalled(first.error)) return { kind: 'plain', micro: await o.wallet() }
    return { kind: 'skip', why: `the treasury status could not be read (${String(first.error.message ?? first.error.code).slice(0, 80)})` }
  }
  const s0 = first.data as TreasuryStatus
  if (!s0?.active || !s0.wallet || s0.wallet.toLowerCase() !== o.forWallet.toLowerCase()) return { kind: 'plain', micro: await o.wallet() }

  await o.refresh().catch(() => {})
  const sync = async () => { const r = await db.rpc('treasury_sync'); if (r.error) throw new Error(`treasury sync: ${String(r.error.message ?? r.error.code).slice(0, 80)}`) }
  let carvePending = false
  try {
    await sync()
    if (o.due) {
      const c = await db.rpc('treasury_carve')
      const status = c.data?.status
      if (c.error || !['carved', 'already', 'inactive'].includes(status)) carvePending = true // stale history or an error: try again next run
    }
    const headBefore = await o.head()
    const walletMicro = await o.wallet()
    await sync() // anything that landed while the wallet was being read
    const st = await db.rpc('treasury_status', { p_head_block: Number(headBefore) })
    if (st.error) throw new Error(`treasury status: ${String(st.error.message ?? st.error.code).slice(0, 80)}`)
    const p = postedValue(walletMicro, st.data as TreasuryStatus, o.forWallet)
    return p.kind === 'value' ? { ...p, carvePending } : p
  } catch (e) {
    return { kind: 'skip', why: `the treasury could not be brought up to date (${(e as Error).message.split('\n')[0]})` }
  }
}
