// Careful mode's pool guards: chain reads and history. The rules are in guard-math.ts.
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { parseAbi, type Address } from 'viem'
import { pub } from './wallet.js'
import { TOKENS, UNI, type TokenKey } from './config.js'
import { addSample, liquidityOk, priceMove, LIQ_SAMPLE_GAP_MS, LIQ_WINDOW_MS, VOL_MAX_MOVE, VOL_WINDOW_MS, type Sample } from './guard-math.js'

// ---------- chain reads ----------
const factoryAbi = parseAbi(['function getPool(address,address,uint24) view returns (address)'])
const poolAbi = parseAbi([
  'function liquidity() view returns (uint128)',
  'function slot0() view returns (uint160 sqrtPriceX96, int24 tick, uint16, uint16, uint16, uint8, bool)',
])
const pools = new Map<string, Address>()
const FILE = (process.env.DATA_DIR ?? '.') + '/pool-history.json'
type Hist = { liq: Sample[]; price: Sample[] }
const hist: Record<string, Hist> = existsSync(FILE) ? (() => { try { return JSON.parse(readFileSync(FILE, 'utf8')) } catch { return {} } })() : {}

/** Checks the Uniswap pool for X/USDT at this fee tier. Never throws: a failed read lets the trade through. */
export async function poolGuard(X: TokenKey, fee: number): Promise<{ ok: boolean; reason?: string }> {
  const key = `${X}-${fee}`
  try {
    let pool = pools.get(key)
    if (!pool) {
      pool = await pub.readContract({ address: UNI.FACTORY, abi: factoryAbi, functionName: 'getPool', args: [TOKENS[X].address, TOKENS.USDT.address, fee] })
      pools.set(key, pool)
    }
    const [liq, slot0] = await Promise.all([
      pub.readContract({ address: pool, abi: poolAbi, functionName: 'liquidity' }),
      pub.readContract({ address: pool, abi: poolAbi, functionName: 'slot0' }),
    ])
    const now = Date.now(), l = Number(liq), sp = Number(slot0[0]) / 2 ** 96, price = sp * sp
    const h = (hist[key] ??= { liq: [], price: [] })
    const liqCheck = liquidityOk(h.liq, now, l)
    const move = priceMove(h.price, now, price)
    h.liq = addSample(h.liq, { t: now, v: l }, LIQ_SAMPLE_GAP_MS, LIQ_WINDOW_MS)
    h.price = addSample(h.price, { t: now, v: price }, 0, VOL_WINDOW_MS)
    try { writeFileSync(FILE, JSON.stringify(hist)) } catch {}
    if (!liqCheck.ok) return { ok: false, reason: `pool liquidity ${Math.round((l / liqCheck.normal!) * 100)}% of its 7-day normal` }
    if (move > VOL_MAX_MOVE) return { ok: false, reason: `pool price moved ${(move * 100).toFixed(2)}% in 10 minutes` }
    return { ok: true }
  } catch {
    return { ok: true }
  }
}
