// Pool guards for careful mode (added 28 Nov after the Season 2 earnings investigation, docs/earnings-investigation.pdf):
//   - liquidity guard: skip a Uniswap pool whose liquidity is under half its normal level (the median of the last
//     7 days). On 14, 17 and 19 Nov most of the wARS pool's liquidity was pulled for hours.
//   - volatility guard: skip a pool whose price moved more than 0.5% in the last 10 minutes. The peso and naira
//     losing days (18, 20, 23, 25, 27-28 Nov) were wide-range or one-way days; arbitrage contracts close gaps in the
//     same block, and a two-leg trade pays for the move halfway through.
// The decisions are pure functions (unit tested in test/guards.test.ts); guards.ts reads the chain and keeps history.

export const LIQ_WINDOW_MS = 7 * 86_400_000
export const LIQ_SAMPLE_GAP_MS = 10 * 60_000
export const LIQ_MIN_SHARE = 0.5
export const LIQ_MIN_SAMPLES = 6 // an hour of history before the liquidity guard can block anything
export const VOL_WINDOW_MS = 10 * 60_000
export const VOL_MAX_MOVE = 0.005

export type Sample = { t: number; v: number }

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b), m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

/** Is the pool's liquidity now at least half its 7-day median? True while there is too little history to judge. */
export function liquidityOk(hist: Sample[], now: number, liqNow: number) {
  const recent = hist.filter((s) => now - s.t <= LIQ_WINDOW_MS).map((s) => s.v)
  if (recent.length < LIQ_MIN_SAMPLES) return { ok: true, normal: null as number | null }
  const normal = median(recent)
  return { ok: liqNow >= LIQ_MIN_SHARE * normal, normal }
}

/** Largest price range over the last 10 minutes, including now, as a share of the low. */
export function priceMove(hist: Sample[], now: number, priceNow: number) {
  const xs = [...hist.filter((s) => now - s.t <= VOL_WINDOW_MS).map((s) => s.v), priceNow]
  const lo = Math.min(...xs), hi = Math.max(...xs)
  return lo > 0 ? (hi - lo) / lo : 0
}

/** Add a sample, keeping at most one per gap and only those inside the window. */
export function addSample(hist: Sample[], s: Sample, gapMs: number, windowMs: number) {
  const last = hist[hist.length - 1]
  const out = last && s.t - last.t < gapMs ? hist : [...hist, s]
  return out.filter((x) => s.t - x.t <= windowMs)
}

