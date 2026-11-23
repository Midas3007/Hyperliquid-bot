// Midas's addition: time-of-day sizing. Pure logic, no network: unit tested in test/schedule.test.ts.
//
// From 10 days of both wallets' trades (Oct 25 to Nov 5, 2026; see docs/trade-size-analysis.pdf):
//   - 13:00 to 14:59 Lagos time (Latin American FX open) made 37% of all profit, with 9 to 11 bps gaps. A
//     bigger trade pays there: Uniswap slippage is about 0.58 bps per $100, so the best size for a 10 bps gap
//     is about $860. The peak size is a multiple of MAX_TRADE_USD, larger on Mondays and Fridays (the best days
//     seen), and never above SMART_PEAK_MAX_USD.
//   - Trades that made under 3 cents were about half of all trades and lost money as a group. Outside the peak
//     the bot asks for a bigger edge, in basis points of the trade size, so it skips the thinnest gaps.
//   - Busy hours (00, 01, 09, 11, 12, 20, 21) keep a mild minimum; quiet hours, and weekends outside the peak,
//     ask for more.
// Switched from Telegram (/smart_on, /smart_off). When off, the bot uses MAX_TRADE_USD and MIN_EDGE_USD as before.

export type Window = 'peak' | 'busy' | 'quiet'
export type Profile = { window: Window; day: string; hour: number; capUsd: number; minEdgeBps: number; minEdgeFloorUsd: number }

export const LAGOS_OFFSET_MS = 3_600_000 // UTC+1, no daylight saving
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
export const PEAK_HOURS = [13, 14]
export const BUSY_HOURS = [0, 1, 9, 11, 12, 20, 21]
// Peak size as a multiple of MAX_TRADE_USD, by Lagos weekday (Sun = 0)
export const PEAK_MULTIPLIER = [1.25, 2, 1.5, 1.5, 1.5, 2, 1.25]
export const RULES: Record<Window, { minEdgeBps: number; minEdgeFloorUsd: number }> = {
  peak: { minEdgeBps: 0.3, minEdgeFloorUsd: 0.01 }, // take nearly everything: gaps are wide
  busy: { minEdgeBps: 1, minEdgeFloorUsd: 0.02 },
  quiet: { minEdgeBps: 1.5, minEdgeFloorUsd: 0.03 },
}

// Careful mode (Telegram /careful_on|off, on unless switched off; added 23 Nov after a -$6.37 day). Gaps are now thin,
// and trades that cleared by a hair lost money when the naira moved between the two legs, big trades lost more, and
// a wARS trade got stuck because Textile had no buyer for wARS. Careful mode, on top of whatever sizing is active:
//   - a higher minimum edge per window (below), never lower than the normal one;
//   - at most CAREFUL_CAP_USD a trade outside the peak hours;
//   - before buying on Uniswap, re-checks Textile's live price for the exact amount and needs the minimum edge plus
//     CAREFUL_UNI_FIRST_CUSHION_BPS (Textile's firm price is usually a little worse than its preview);
//   - trades only CAREFUL_CORRIDORS (wBRL is out: its Uniswap pool is empty; wARS stays, guarded by the Textile re-check).
export const CAREFUL_RULES: Record<Window, { minEdgeBps: number; minEdgeFloorUsd: number }> = {
  peak: { minEdgeBps: 2, minEdgeFloorUsd: 0.05 },
  busy: { minEdgeBps: 3, minEdgeFloorUsd: 0.05 },
  quiet: { minEdgeBps: 4, minEdgeFloorUsd: 0.05 },
}
export const CAREFUL_CAP_USD = 500
export const CAREFUL_UNI_FIRST_CUSHION_BPS = 2
export const CAREFUL_CORRIDORS = ['cNGN', 'USDC', 'wARS']

/** Which window a moment falls in (peak, busy or quiet), whether or not smart sizing is on. */
export function windowAt(now: Date): Window {
  const { dow, hour } = lagosTime(now)
  const weekend = dow === 0 || dow === 6
  return PEAK_HOURS.includes(hour) ? 'peak' : !weekend && BUSY_HOURS.includes(hour) ? 'busy' : 'quiet'
}

/** Careful mode's minimum edge and cap on top of the normal ones (never looser than them). */
export function carefulLimits(now: Date, normalMinEdgeUsd: (sizeUsd: number) => number, normalCapUsd: number) {
  const window = windowAt(now)
  return {
    window,
    capUsd: window === 'peak' ? normalCapUsd : Math.min(normalCapUsd, CAREFUL_CAP_USD),
    minEdge: (sizeUsd: number) => Math.max(normalMinEdgeUsd(sizeUsd), minEdgeFor(sizeUsd, CAREFUL_RULES[window])),
  }
}

/** Lagos weekday (0 = Sunday) and hour for a moment in time. */
export function lagosTime(now: Date) {
  const t = new Date(now.getTime() + LAGOS_OFFSET_MS)
  return { dow: t.getUTCDay(), hour: t.getUTCHours() }
}

/** The sizing and minimum edge for this moment. baseCapUsd is MAX_TRADE_USD; peakMaxUsd bounds the peak size. */
export function smartProfile(now: Date, baseCapUsd: number, peakMaxUsd: number): Profile {
  const { dow, hour } = lagosTime(now)
  const weekend = dow === 0 || dow === 6
  const window: Window = PEAK_HOURS.includes(hour) ? 'peak' : !weekend && BUSY_HOURS.includes(hour) ? 'busy' : 'quiet'
  const capUsd = window === 'peak' ? Math.max(baseCapUsd, Math.min(baseCapUsd * PEAK_MULTIPLIER[dow], peakMaxUsd)) : baseCapUsd
  return { window, day: DAYS[dow], hour, capUsd: Math.round(capUsd * 100) / 100, ...RULES[window] }
}

/** Smallest acceptable expected profit (USD) for a trade of this size under the profile. */
export const minEdgeFor = (sizeUsd: number, p: Pick<Profile, 'minEdgeBps' | 'minEdgeFloorUsd'>) =>
  Math.max(p.minEdgeFloorUsd, (sizeUsd * p.minEdgeBps) / 10_000)

/** One line for logs and Telegram. */
export const describe = (p: Profile) =>
  `${p.window} (${p.day} ${String(p.hour).padStart(2, '0')}:00 Lagos): max trade $${p.capUsd.toFixed(0)}, min edge ${p.minEdgeBps} bps (at least $${p.minEdgeFloorUsd.toFixed(2)})`

/** The whole week as text, for Telegram's /schedule. */
export function scheduleTable(baseCapUsd: number, peakMaxUsd: number) {
  const fmt = (h: number[]) => h.map((x) => String(x).padStart(2, '0')).join(', ')
  const peak = DAYS.map((d, i) => `${d} $${Math.max(baseCapUsd, Math.min(baseCapUsd * PEAK_MULTIPLIER[i], peakMaxUsd)).toFixed(0)}`).join(' · ')
  return [
    `Peak 13:00–14:59 every day: min edge ${RULES.peak.minEdgeBps} bps (≥ $${RULES.peak.minEdgeFloorUsd}); max trade ${peak}`,
    `Busy ${fmt(BUSY_HOURS)}h on weekdays: max trade $${baseCapUsd}, min edge ${RULES.busy.minEdgeBps} bp (≥ $${RULES.busy.minEdgeFloorUsd})`,
    `Quiet (every other hour, and weekends outside the peak): max trade $${baseCapUsd}, min edge ${RULES.quiet.minEdgeBps} bps (≥ $${RULES.quiet.minEdgeFloorUsd})`,
  ].join('\n')
}
