"""Backtest (v3.3): replay Hyperliquid price history through HEVI's real bracket rules.

What it tests, honestly:
  * The exits. At every hour Mon-Thu UTC it asks the live code (levels.build_levels, rules/bracket.py,
    rules/sizing.py, rules/schedule.py) for a stop beyond 4H structure, a target at the first 4H level, the ADR
    reach limit, the net 1:3 floor after fees/slippage/funding, then walks the following hourly candles to the
    TP, the SL, the mode's time stop or the Friday 20:00 UTC flatten.
  * Not the entry filters. Hyperliquid keeps no history of buy/sell flow, whale positions, open interest or news,
    so those can't be replayed. Direction is either "both" (every setup, long and short) or "trend" (the side
    of the last 24h move, the crude stand-in for HEVI's momentum and flow sweeps). Real HEVI is pickier.

Assumptions: entry fills at the hourly close (HEVI rests a post-only limit at a 5m level near price; 5m history only
goes back ~17 days), maker fee in, taker fee + slippage out, funding at Hyperliquid's 0.01%/8h baseline, and when one
candle touches both the SL and the TP the SL counts (worst case). R = profit / loss at the stop, so an SL is -1R.
Hyperliquid serves the last 5000 candles per interval, so the 1h replay covers roughly the last 200 days.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import levels as lv
from .protocol import PROTOCOL as P
from .rules import bracket, metrics as mx, schedule as sch
from .rules.sizing import Fees, SizingInput, round_price_dir, size

H_MS = 3_600_000
FOUR_H_MS = 4 * H_MS
DAY_MS = 24 * H_MS
BASE_FUNDING_8H = 0.01  # % per 8h, Hyperliquid's interest-rate baseline (longs pay)


@dataclass(frozen=True)
class Variant:
    name: str
    max_adr_mult: float
    hold_h: float


def current_variants() -> list[Variant]:
    s = P.strategy
    return [Variant("swing", s.swing.max_adr_mult, s.swing.hold_h), Variant("day", s.day.max_adr_mult, s.day.hold_h)]


def sweep_variants() -> list[Variant]:
    return [Variant(f"adr{a:g}/{h:g}h", a, h) for a in (0.8, 1.0, 1.2, 1.5) for h in (24.0, 48.0)]


@dataclass
class Setup:
    coin: str
    t: int                # decision time (ms, the hourly close)
    side: str
    variant: str
    entry: float
    sl: float
    tp: float
    net_rr: float
    exit_t: int | None = None
    exit_type: str = ""
    r: float | None = None
    trend: bool = False   # on the side of the last 24h move
    exit_px: float | None = None


@dataclass
class Report:
    coins: list[str]
    start: int
    end: int
    decisions: int = 0
    setups: list[Setup] = field(default_factory=list)
    reasons: dict = field(default_factory=lambda: defaultdict(Counter))   # variant -> why no setup


# ------------------------------------------------------------------ candles
def agg(cands: list[dict]) -> dict | None:
    if not cands:
        return None
    return {"t": cands[0]["t"], "o": cands[0]["o"], "h": max(c["h"] for c in cands), "l": min(c["l"] for c in cands),
            "c": cands[-1]["c"], "v": sum(c["v"] for c in cands)}


class Series:
    """Price history of one coin, sliced 'as of' a decision time the way the live bot would have seen it."""

    def __init__(self, c1h: list[dict], c4h: list[dict], c1d: list[dict]):
        self.c1h, self.c4h, self.c1d = c1h, c4h, c1d
        self._t4 = [c["t"] for c in c4h]
        self._td = [c["t"] for c in c1d]

    @staticmethod
    def _upto(ts: list[int], t: int) -> int:
        """Number of candles that started before t."""
        lo, hi = 0, len(ts)
        while lo < hi:
            mid = (lo + hi) // 2
            if ts[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def as_of(self, i: int) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        """(completed 4h, 4h incl. the forming one, completed 1d, 1d incl. the forming one) at the close of 1h candle
        i. Forming candles are rebuilt from the hourly candles of the current block, so nothing from the future leaks."""
        t = self.c1h[i]["t"] + H_MS
        b4, bd = t // FOUR_H_MS * FOUR_H_MS, t // DAY_MS * DAY_MS
        c4_done = self.c4h[max(0, self._upto(self._t4, b4) - 120): self._upto(self._t4, b4)]
        d_done = self.c1d[max(0, self._upto(self._td, bd) - 60): self._upto(self._td, bd)]
        lo = max(0, i - 23)
        f4 = agg([c for c in self.c1h[lo: i + 1] if c["t"] >= b4])
        fd = agg([c for c in self.c1h[lo: i + 1] if c["t"] >= bd])
        return c4_done, c4_done + ([f4] if f4 else []), d_done, d_done + ([fd] if fd else [])


# ------------------------------------------------------------------ one decision
def _why(text: str) -> str:
    if text.startswith("N/A"):
        return "no ATR/ADR yet"
    if "structure" in text:
        return "SL: no 4H structure behind entry"
    if "too close" in text:
        return "TP: first 4H level too close"
    if "anchor the TP" in text:
        return "TP: no 4H level ahead"
    return text[:40]


def plan(side: str, price: float, struct, atr4: float | None, adr: float | None, szd: int, tiers,
         fees: Fees) -> tuple[tuple | None, str]:
    """The live bracket + sizing rules for one side at one moment, before the mode's ADR reach limit (the TP level
    doesn't depend on it; the limit only rejects). -> ((entry, sl, tp, sizing, tp_adr_mult), "ok") or (None, why)."""
    up = side == "short"
    entry = round_price_dir(price, szd, up=up)
    sl, how = bracket.stop_loss(entry, side, struct, atr4, P.risk)
    if sl is None:
        return None, _why(how)
    tpp, why = bracket.take_profit(entry, price, side, struct, atr4 / price * 100 if atr4 else None, adr, P.tp,
                                   max_adr_mult=1e9)
    if tpp is None:
        return None, _why(why)
    sl = round_price_dir(sl, szd, up=up)
    tp = round_price_dir(tpp.tp, szd, up=up)
    z = size(SizingInput(side, entry, sl, tp, 100.0, 100.0, P.risk.normal_pct, False, szd, tiers, fees,
                         BASE_FUNDING_8H), P.risk, P.exchange, P.filters)
    return (entry, sl, tp, z, tpp.adr_mult), "ok"


def walk(c1h: list[dict], i: int, side: str, entry: float, sl: float, tp: float, z, hold_h: float,
         fees: Fees) -> tuple[int | None, str, float | None, float | None]:
    """Follow hourly candles after the fill at the close of candle i. -> (exit time ms, exit type, R, exit price)."""
    t_fill = c1h[i]["t"] + H_MS
    stop = sch.time_stop_at(t_fill / 1000, P.schedule, hold_h) * 1000
    s = 1 if side == "long" else -1
    risk = z.loss_at_sl
    for c in c1h[i + 1:]:
        end = c["t"] + H_MS
        hit_sl = c["l"] <= sl if side == "long" else c["h"] >= sl
        hit_tp = c["h"] >= tp if side == "long" else c["l"] <= tp
        if hit_sl:  # also when the same candle touched the TP: worst case
            return end, "sl", -1.0, sl
        if hit_tp:
            return end, "tp", z.net_rr, tp
        if end >= stop:
            px = c["c"]
            held_h = (end - t_fill) / H_MS
            pnl = (s * (px - entry) * z.qty - z.entry_fee - z.qty * px * (fees.taker_pct + fees.slippage_pct) / 100
                   - max(0.0, s * BASE_FUNDING_8H / 100 / 8 * z.notional * held_h))
            return end, ("friday" if stop < t_fill + hold_h * H_MS else "time"), pnl / risk, px
    return None, "open", None, None  # the data ends before the trade does


def run_coin(coin: str, ser: Series, variants: list[Variant], szd: int, tiers, fees: Fees, rep: Report,
             start_ms: int) -> None:
    c1h = ser.c1h
    dd = P.deep_dive
    for i in range(len(c1h)):
        t = c1h[i]["t"] + H_MS
        if t < start_ms or i < 24 or not sch.entry_window_open(t / 1000, P.schedule):
            continue
        c4_done, c4, d_done, c1d = ser.as_of(i)
        if len(c4_done) < 90 or len(d_done) < 30:
            continue
        price = c1h[i]["c"]
        levels = lv.build_levels([], c4, c1d)
        struct = bracket.structural(levels["4h"], P.tp)
        atr4 = mx.atr(c4_done, dd.atr_period)
        adr = mx.adr_pct(d_done, dd.adr_days)
        rep.decisions += 1
        trend = "long" if price > c1h[i - 24]["c"] else "short"
        for side in ("long", "short"):
            got, why = plan(side, price, struct, atr4, adr, szd, tiers, fees)
            walked: dict = {}
            for v in variants:
                if not got:
                    rep.reasons[v.name][why] += 1
                    continue
                entry, sl, tp, z, adr_mult = got
                if adr_mult > v.max_adr_mult:
                    rep.reasons[v.name]["TP: beyond the ADR reach"] += 1
                    continue
                if not z.ok:
                    rep.reasons[v.name]["sizing: " + next(c for c in z.checks if not c.ok).rule] += 1
                    continue
                if v.hold_h not in walked:
                    walked[v.hold_h] = walk(c1h, i, side, entry, sl, tp, z, v.hold_h, fees)
                exit_t, kind, r, px = walked[v.hold_h]
                rep.setups.append(Setup(coin, t, side, v.name, entry, sl, tp, z.net_rr, exit_t, kind, r, side == trend, px))


# ------------------------------------------------------------------ results
def one_at_a_time(setups: list[Setup], per_coin: bool) -> list[Setup]:
    """Take setups in time order, never overlapping (per coin, or account-wide like HEVI's 1-position rule), and
    stop for the UTC day after an SL (the protocol's first breaker)."""
    taken: list[Setup] = []
    busy: dict = {}
    sl_day: dict = {}
    for s in sorted(setups, key=lambda s: (s.t, -s.net_rr)):
        if s.r is None:
            continue
        key = s.coin if per_coin else "all"
        if s.t < busy.get(key, 0) or sl_day.get(key) == s.t // DAY_MS:
            continue
        taken.append(s)
        busy[key] = s.exit_t
        if s.exit_type == "sl":
            sl_day[key] = s.exit_t // DAY_MS
    return taken


def stats(trades: list[Setup]) -> dict:
    rs = [t.r for t in trades if t.r is not None]
    if not rs:
        return {"n": 0}
    wins = sum(r for r in rs if r > 0)
    losses = -sum(r for r in rs if r < 0)
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    kinds = Counter(t.exit_type for t in trades)
    return {"n": len(rs), "tp": kinds["tp"], "sl": kinds["sl"], "timed": kinds["time"] + kinds["friday"],
            "win_pct": 100 * sum(1 for r in rs if r > 0) / len(rs), "exp_r": sum(rs) / len(rs), "total_r": sum(rs),
            "pf": wins / losses if losses else float("inf"), "max_dd_r": dd}


def fmt_stats(name: str, s: dict, weeks: float) -> str:
    if not s.get("n"):
        return f"{name}: no trades"
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"{name}: {s['n']} trades ({s['n'] / weeks:.1f}/wk) | TP {s['tp']} SL {s['sl']} timed {s['timed']} | "
            f"win {s['win_pct']:.0f}% | {s['exp_r']:+.2f}R/trade | PF {pf} | total {s['total_r']:+.1f}R | "
            f"max DD {s['max_dd_r']:.1f}R")


def summarize(rep: Report, variants: list[Variant], split: bool = True) -> str:
    weeks = max(1e-9, (rep.end - rep.start) / (7 * DAY_MS))
    day = lambda ms: time.strftime("%d %b %Y", time.gmtime(ms / 1000))  # noqa: E731
    out = [f"HEVI backtest - Protocol v{P.version}, {len(rep.coins)} coins, {day(rep.start)} to {day(rep.end)} "
           f"({weeks:.0f} weeks), {rep.decisions} coin-hours Mon-Thu",
           "Tests the exits (SL/TP/time/Friday rules) on real prices. Entry filters (flow, whales, OI, news) have no "
           "history, so 'trend' = trade the side of the last 24h move.", ""]
    for v in variants:
        mine = [s for s in rep.setups if s.variant == v.name]
        n_dec = rep.decisions * 2
        out.append(f"[{v.name}] TP <= {v.max_adr_mult:g}x ADR, hold {v.hold_h:g}h: a valid bracket in "
                   f"{100 * len(mine) / max(1, n_dec):.1f}% of coin-hours per side")
        why = rep.reasons[v.name]
        if why:
            tot = sum(why.values())
            out.append("  no bracket: " + ", ".join(f"{k} {100 * n / tot:.0f}%" for k, n in why.most_common(4)))
        trend = [s for s in mine if s.trend]
        out.append("  " + fmt_stats("every setup, per coin", stats(one_at_a_time(mine, True)), weeks))
        out.append("  " + fmt_stats("trend side, per coin", stats(one_at_a_time(trend, True)), weeks))
        acct = one_at_a_time(trend, False)
        out.append("  " + fmt_stats("trend side, 1 position like HEVI", stats(acct), weeks))
        if split and acct:
            cut = rep.start + (rep.end - rep.start) * 2 // 3
            a = stats([s for s in acct if s.t < cut])
            b = stats([s for s in acct if s.t >= cut])
            if a.get("n") and b.get("n"):
                out.append(f"  first 2/3: {a['exp_r']:+.2f}R/trade ({a['n']}) | last 1/3: {b['exp_r']:+.2f}R/trade "
                           f"({b['n']}) - an edge should hold in both")
        out.append("")
    return "\n".join(out).rstrip()


# ------------------------------------------------------------------ data
def universe(ctxs: dict, top: int) -> list[str]:
    stable = set(P.screener.stablecoins)
    rows = [(d["volume_usd"], c) for c, d in ctxs.items()
            if c not in stable and d.get("volume_usd", 0) >= P.screener.sweep1_min_volume_usd]
    return [c for _, c in sorted(rows, reverse=True)[:top]]


def fetch(info, coin: str, now_ms: int) -> Series:
    def get(interval, n, step):
        rows = info.candles_snapshot(coin, interval, now_ms - n * step, now_ms)
        out = [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]),
                "v": float(c["v"])} for c in rows]
        return out

    c1h = get("1h", 5000, H_MS)
    c1h = [c for c in c1h if c["t"] + H_MS <= now_ms]  # completed hours only
    c4h = get("4h", 1600, FOUR_H_MS)
    c1d = get("1d", 450, DAY_MS)
    return Series(c1h, c4h, c1d)


def run(info, ctxs: dict, coins: list[str] | None = None, top: int = 15, variants: list[Variant] | None = None,
        pace_s: float = 12.0, log=None) -> Report:
    """Fetch history and replay it. pace_s spaces the candle downloads (~180 request weight per coin) to stay well
    inside Hyperliquid's 1200-per-minute limit while the live bot keeps running."""
    variants = variants or current_variants()
    coins = coins or universe(ctxs, top)
    now_ms = int(time.time() * 1000)
    fees = Fees(P.fees.maker_pct, P.fees.taker_pct, P.fees.slippage_pct, "base tier", P.fees.slippage_on_tp)
    rep = Report(coins, 0, 0)
    starts, ends = [], []
    for k, coin in enumerate(coins):
        if k and pace_s:
            time.sleep(pace_s)
        try:
            ser = fetch(info, coin, now_ms)
        except Exception as e:  # noqa: BLE001
            if log:
                log(f"{coin}: history unavailable ({str(e)[:80]})")
            continue
        if len(ser.c1h) < 24 * 30:
            continue
        start = ser.c1h[0]["t"] + DAY_MS  # the 24h trend needs a day of hourly candles (4h/1d history is longer)
        starts.append(start)
        ends.append(ser.c1h[-1]["t"] + H_MS)
        d = ctxs[coin]
        run_coin(coin, ser, variants, d["sz_decimals"], d["margin_tiers"], fees, rep, start)
        if log:
            log(f"{coin}: done ({k + 1}/{len(coins)})")
    rep.start = min(starts) if starts else now_ms
    rep.end = max(ends) if ends else now_ms
    return rep
