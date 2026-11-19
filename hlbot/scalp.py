"""HEVI scalp mode (protocol scalp-0.1, protocol.toml [scalp]): a separate BTC scalper on its own $100 paper ledger.

Two setups, routed by regime (15m EMA20 slope + side of the UTC-session VWAP):
  * B, trend pullback: in a trend, a 1m candle dips to VWAP / 5m EMA20 and closes back on the trend side -> enter
    with the trend; stop past the pullback low (high).
  * A, sweep & reclaim: in a range, a 1m wick runs the last 2 hours' high (low) and closes back inside -> fade it;
    stop past the wick extreme.
Stops are bounded to 0.20-0.60% and >= 0.8x 5m ATR. Targets: half at 1R net of fees (stop to break-even + fees), rest
at 2R net. Targets are set in net R, so a target never pays less than 1:1 after costs. Time stop: out if not +0.5R within 20 minutes; max hold 90 min; flat at the end of
each session window (07-11, 13-17 UTC). Nothing within 10 min of CPI/FOMC/NFP.

Sizing: risk % of scalp equity at the stop sets the notional (that is what sets profit per move). Isolated leverage is then
the highest the coin allows (BTC 40x) whose liquidation is still >= 3x the stop distance away, so the least margin is
posted per trade; it never changes the $ at risk.

Paper fills (honest by design): a post-only limit fills only when a later 1m candle trades THROUGH it (touch is not
enough), within 2 bars; a candle that touches both stop and target counts as the stop; maker fee in, maker on the
limit TP, taker + slippage on stops, time stops and session exits. Only completed 1m candles are used.

Breakers (own, never shared with swing): -3R day stop, half risk after +4R, 12 trades/day, 3 losses in a row ->
45 min cooldown, 5 losses -> done for the day, drawdown 5% -> half risk, 10% -> paused until the owner resumes.
Gate: 10 closed scalps, >= +0.10R per trade, profit factor >= 1.2. Passing it does NOT switch to real money.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass

from .protocol import PROTOCOL, Scalp
from .rules.sizing import maintenance
from .store import Store

log = logging.getLogger(__name__)
M1 = 60_000
DAY = 86400


# ------------------------------------------------------------------ indicators
def ema(vals: list[float], n: int) -> list[float]:
    if not vals:
        return []
    k, out = 2 / (n + 1), [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def atr_pct(c: list[dict], n: int = 14) -> float | None:
    if len(c) < n + 1:
        return None
    trs = [max(x["h"] - x["l"], abs(x["h"] - p["c"]), abs(x["l"] - p["c"])) for p, x in zip(c[-n - 1:-1], c[-n:])]
    return sum(trs) / n / c[-1]["c"] * 100


def vwap(c: list[dict]) -> float | None:
    v = sum(x["v"] for x in c)
    return sum((x["h"] + x["l"] + x["c"]) / 3 * x["v"] for x in c) / v if v > 0 else None


def agg(c1: list[dict], minutes: int) -> list[dict]:
    out: dict[int, dict] = {}
    for x in c1:
        k = x["t"] // (minutes * M1)
        o = out.get(k)
        if o is None:
            out[k] = dict(x, t=k * minutes * M1)
        else:
            o["h"], o["l"], o["c"], o["v"] = max(o["h"], x["h"]), min(o["l"], x["l"]), x["c"], o["v"] + x["v"]
    return [out[k] for k in sorted(out)]


# ------------------------------------------------------------------ signal
@dataclass
class Signal:
    setup: str          # "A sweep" | "B pullback"
    side: str
    entry: float
    stop: float
    why: str


def regime(c1: list[dict], s: Scalp) -> tuple[str, float | None, float | None]:
    """('up' | 'down' | 'range', session vwap, 5m EMA20) from completed 1m candles of the UTC day so far."""
    day0 = c1[-1]["t"] // (DAY * 1000) * DAY * 1000
    sess = [x for x in c1 if x["t"] >= day0] or c1[-60:]
    vw = vwap(sess)
    c15, c5 = agg(c1, 15), agg(c1, 5)
    e15, e5 = ema([x["c"] for x in c15], 20), ema([x["c"] for x in c5], 20)
    if len(e15) < 24 or vw is None:
        return "range", vw, e5[-1] if e5 else None
    slope = (e15[-1] / e15[-4] - 1) * 100
    px = c1[-1]["c"]
    if slope > s.trend_slope_pct and px > vw:
        return "up", vw, e5[-1]
    if slope < -s.trend_slope_pct and px < vw:
        return "down", vw, e5[-1]
    return "range", vw, e5[-1]


def find_signal(c1: list[dict], s: Scalp) -> tuple[Signal | None, str]:
    """Look at the last completed 1m candle. Returns (signal, reason when none)."""
    tf = max(1, round((c1[-1]["t"] - c1[-2]["t"]) / M1)) if len(c1) > 1 else 1
    if len(c1) < max(130, 400 // tf):          # ~7h of history whatever the timeframe (1m: 400 bars, 5m: 130)
        return None, "warming up (needs ~7h of candles)"
    a5 = atr_pct(agg(c1, 5))
    if a5 is None or not (s.atr_min_pct <= a5 <= s.atr_max_pct):
        return None, f"5m ATR {a5 and round(a5, 3)}% outside {s.atr_min_pct}-{s.atr_max_pct}%"
    reg, vw, e5 = regime(c1, s)
    k = c1[-1]
    rng = max(k["h"] - k["l"], 1e-9)
    buf = k["c"] * 0.0003
    if reg in ("up", "down") and vw and e5:
        lvl = max(vw, e5) if reg == "up" else min(vw, e5)
        if reg == "up" and k["l"] <= lvl * 1.0005 and k["c"] > lvl and k["c"] > k["o"]:
            low = min(x["l"] for x in c1[-5:])
            return Signal("B pullback", "long", k["c"] - 0.25 * (k["c"] - lvl), low - buf, f"uptrend, reclaimed {lvl:,.1f}"), "ok"
        if reg == "down" and k["h"] >= lvl * 0.9995 and k["c"] < lvl and k["c"] < k["o"]:
            high = max(x["h"] for x in c1[-5:])
            return Signal("B pullback", "short", k["c"] + 0.25 * (lvl - k["c"]), high + buf, f"downtrend, rejected {lvl:,.1f}"), "ok"
        return None, f"{reg}trend: waiting for a pullback to VWAP/EMA"
    hi, lo = max(x["h"] for x in c1[-123:-3]), min(x["l"] for x in c1[-123:-3])
    if k["h"] > hi and k["c"] < hi and (k["h"] - max(k["o"], k["c"])) / rng >= 0.5:
        return Signal("A sweep", "short", k["c"], k["h"] + buf, f"swept 2h high {hi:,.1f} and reclaimed"), "ok"
    if k["l"] < lo and k["c"] > lo and (min(k["o"], k["c"]) - k["l"]) / rng >= 0.5:
        return Signal("A sweep", "long", k["c"], k["l"] - buf, f"swept 2h low {lo:,.1f} and reclaimed"), "ok"
    return None, f"range {lo:,.1f}-{hi:,.1f}: no sweep"


# ------------------------------------------------------------------ sizing
@dataclass
class Size:
    ok: bool
    why: str
    qty: float = 0.0
    notional: float = 0.0
    leverage: int = 0
    stop: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    net_rr: float = 0.0
    risk_usd: float = 0.0
    liq: float = 0.0


def size(side: str, entry: float, stop: float, equity: float, risk_pct: float, atr5: float, tiers, szd: int,
         s: Scalp) -> Size:
    sgn = 1 if side == "long" else -1
    dist = abs(entry - stop) / entry * 100
    dist = max(dist, s.stop_min_pct, s.stop_atr_mult * atr5)
    if dist > s.stop_max_pct:
        return Size(False, f"stop {dist:.2f}% > {s.stop_max_pct}% band")
    stop = entry * (1 - sgn * dist / 100)
    cost = s.maker_pct + s.taker_pct + s.slippage_pct            # all-in % of notional, entry + stop exit
    risk = equity * min(risk_pct, s.risk_cap_pct) / 100
    notional = risk / ((dist + cost) / 100)
    qty = math.floor(notional / entry * 10 ** szd) / 10 ** szd
    if qty <= 0 or qty * entry < 10:
        return Size(False, "below Hyperliquid's $10 minimum")
    notional = qty * entry
    mm, coin_max, mmr = maintenance(notional, tiers)
    cap = min(s.max_leverage, coin_max)                         # the coin's own max at this size also caps it
    need = max(1, math.ceil(notional / equity))                 # the least leverage that fits the margin
    if need > cap:
        return Size(False, f"needs more than {cap}x")
    # leverage only sets the margin posted (profit per trade comes from risk %): use the highest leverage whose
    # isolated liquidation is still >= liq_buffer_mult x the stop away, so the least capital is tied up per trade
    lev = liq = None
    for L in range(cap, need - 1, -1):
        margin = notional / L
        if margin <= mm:
            continue
        lq = entry - sgn * (margin - mm) / qty / (1 - sgn * mmr)
        if abs(entry - lq) / entry * 100 >= s.liq_buffer_mult * dist:
            lev, liq = L, lq
            break
    if lev is None:
        return Size(False, f"no leverage up to {cap}x keeps liquidation >= {s.liq_buffer_mult:g}x the {dist:.2f}% stop away")
    loss = notional * (dist + cost) / 100
    # targets are placed in NET R: k x (stop distance + all costs) + the maker fees of the limit exit, so a target of
    # kR really pays k times what the stop would cost. Never below 1:1 (min_net_rr >= 1).
    tgt = lambda k: entry * (1 + sgn * (k * (dist + cost) + 2 * s.maker_pct) / 100)  # noqa: E731
    tp1, tp2 = tgt(s.tp1_r), tgt(s.tp2_r)
    net_rr = s.tp2_r
    if net_rr < max(1.0, s.min_net_rr):
        return Size(False, f"target {net_rr:.2f}R net < {max(1.0, s.min_net_rr):g}R")
    return Size(True, "ok", qty, notional, lev, stop, tp1, tp2, net_rr, loss, liq)


# ------------------------------------------------------------------ engine
class ScalpEngine:
    """Paper scalper. tick() is called every ~20 s; it only acts on newly completed 1m candles."""

    live = False

    def __init__(self, cfg, mkt, P=PROTOCOL, s: Scalp | None = None, name: str = "scalp", veto=None):
        self.s: Scalp = s or P.scalp
        self.P = P
        self.mkt = mkt
        self.name = name
        self.veto = veto            # callable(payload) -> {"verdict": take|skip|downsize, "risk_multiplier": <=1, ...}
        d = os.path.join(cfg.data_dir, "scalp") if name == "scalp" else os.path.join(cfg.data_dir, "arms", name)
        os.makedirs(d, exist_ok=True)
        self.store = Store(d, "scalp.db")
        st = self.store.get("scalp_state")
        if not st:
            st = {"cash": self.s.paper_balance, "start": self.s.paper_balance, "reset_at": time.time(), "hwm": self.s.paper_balance,
                  "last_t": 0, "paused": False, "halted": False, "cooldown_until": 0}
            self.store.set("scalp_state", st)
            self.store.record_equity(self.s.paper_balance)
        self.st = st
        self.last_reason = "starting"

    # -- publisher / status interface (record.build) ------------------------
    def _since(self) -> float:
        return self.st["reset_at"]

    @property
    def paused(self) -> bool:
        return bool(self.st["paused"] or self.st["halted"])

    def label(self) -> str:
        return f"SCALP {self.s.version} - BTC paper scalper (separate $100 ledger)"

    def strategy_label(self) -> str:
        return "scalp (A sweep / B pullback)"

    def go_live_status(self) -> str:
        return "scalp gate: " + self.gate()[1]

    def _save(self) -> None:
        self.store.set("scalp_state", self.st)

    # -- helpers ------------------------------------------------------------
    def equity(self) -> float:
        return float(self.st["cash"])

    def closed_today(self, now: float) -> list[dict]:
        return self.store.closed(max(now // DAY * DAY, self._since()))

    def gate(self) -> tuple[bool, str]:
        rs = [t["result_r"] for t in self.store.closed(self._since()) if t.get("result_r") is not None]
        n, s = len(rs), self.s
        if n < s.gate_min_trades:
            return False, f"{n}/{s.gate_min_trades} scalps so far"
        exp = sum(rs) / n
        w, l = sum(r for r in rs if r > 0), -sum(r for r in rs if r <= 0)
        pf = w / l if l > 0 else float("inf")
        if exp < s.gate_min_expectancy_r:
            return False, f"expectancy {exp:+.2f}R over {n} (needs >= {s.gate_min_expectancy_r:+.2f}R)"
        if pf < s.gate_min_profit_factor:
            return False, f"profit factor {pf:.2f} over {n} (needs >= {s.gate_min_profit_factor:g})"
        return True, f"PASSED: {n} scalps, {exp:+.2f}R/trade, PF {pf:.2f} (paper; live needs your explicit go)"

    def in_session(self, now: float) -> tuple[bool, float]:
        h = (now % DAY) / 3600
        w = self.s.sessions_utc
        for a, b in zip(w[0::2], w[1::2]):
            if a <= h < b:
                return True, now // DAY * DAY + b * 3600
        return False, 0.0

    def risk_now(self, now: float) -> tuple[float, list[str]]:
        """Risk % for a new trade and the reasons it's blocked (empty = allowed)."""
        s, block, risk = self.s, [], self.s.risk_pct
        today = self.closed_today(now)
        rs = [t["result_r"] or 0 for t in today]
        if self.st["halted"]:
            block.append("halted by owner (/scalp resume)")
        if self.st["paused"]:
            block.append(f"paused: drawdown >= {s.dd_pause_pct:g}% (/scalp resume)")
        if len(today) >= s.max_trades_day:
            block.append(f"{s.max_trades_day} scalps today")
        if sum(rs) <= s.daily_stop_r:
            block.append(f"day stop at {sum(rs):+.1f}R")
        if sum(1 for r in rs if r < 0) >= s.max_losses_day:
            block.append(f"{s.max_losses_day} losses today")
        if now < self.st.get("cooldown_until", 0):
            block.append(f"cooldown after {s.loss_streak_cooldown} losses until {time.strftime('%H:%M', time.gmtime(self.st['cooldown_until']))} UTC")
        dd = (1 - self.equity() / max(self.st["hwm"], 1e-9)) * 100
        if sum(rs) >= s.daily_lock_r or dd >= s.dd_half_pct:
            risk /= 2
        return risk, block

    # -- main loop ------------------------------------------------------------
    def tick(self, now: float | None = None) -> list[str]:
        if not self.s.enabled:
            return []
        now = now or time.time()
        c1 = self.mkt.candles(self.s.coin, "1m", 1500)
        c1 = [x for x in c1 if x["t"] + M1 <= now * 1000]            # completed only
        if not c1 or now * 1000 - (c1[-1]["t"] + M1) > 3 * M1:
            self.last_reason = "1m data stale"
            return []
        new = [x for x in c1 if x["t"] > self.st["last_t"]]
        msgs: list[str] = []
        for k in new:                                               # replay every new candle in order
            msgs += self._manage(k, c1, now)
        if new:
            self.st["last_t"] = new[-1]["t"]
            self._save()
            msgs += self._maybe_enter(c1, now)
        return msgs

    def _maybe_enter(self, c1: list[dict], now: float) -> list[str]:
        s = self.s
        if self.store.trades("open") or self.store.trades("pending"):
            return []
        ok, end = self.in_session(now)
        if not ok:
            self.last_reason = "outside the 07-11 / 13-17 UTC session windows"
            return []
        if end - now < 10 * 60:
            self.last_reason = "less than 10 min left in this session"
            return []
        try:
            events, _cov = self.mkt.macro_calendar(("CPI", "FOMC", "NFP"))
        except Exception:  # noqa: BLE001
            events = []
        if any(abs(t - now) < s.macro_buffer_min * 60 for _, t in events):
            self.last_reason = "macro release within 10 minutes"
            return []
        risk, block = self.risk_now(now)
        if block:
            self.last_reason = "; ".join(block)
            return []
        tf = max(1, int(s.signal_tf))
        if tf > 1 and (c1[-1]["t"] + M1) % (tf * M1):
            self.last_reason = f"waiting for the {tf}m candle to close"
            return []
        bars = c1 if tf == 1 else agg(c1, tf)
        sig, why = find_signal(bars, s) if tf == 1 or len(bars) >= 130 else (None, "warming up")
        if not sig:
            self.last_reason = why
            return []
        ctx = self.mkt.ctxs()[s.coin]
        z = size(sig.side, sig.entry, sig.stop, self.equity(), risk, atr_pct(agg(c1, 5)) or 0, ctx["margin_tiers"],
                 ctx["sz_decimals"], s)
        if not z.ok:
            self.last_reason = f"{sig.setup} {sig.side} skipped: {z.why}"
            return []
        if s.claude_veto and self.veto:
            v = self._ask_veto(sig, z, c1, tf)
            if v is not None:
                verdict, mult = v.get("verdict"), min(1.0, max(0.0, float(v.get("risk_multiplier", 1.0))))
                if verdict == "skip" or mult <= 0:
                    self.last_reason = f"{sig.setup} {sig.side} vetoed by Claude: {'; '.join(v.get('reasons', []))[:120]}"
                    return []
                if verdict == "downsize" and mult < 1:
                    z = size(sig.side, sig.entry, sig.stop, self.equity(), risk * mult, atr_pct(agg(c1, 5)) or 0,
                             ctx["margin_tiers"], ctx["sz_decimals"], s)
                    if not z.ok:
                        self.last_reason = f"downsized by Claude below the minimum: {z.why}"
                        return []
        tid = self.store.add_trade(coin=s.coin, side=sig.side, status="pending", entry=sig.entry, sl=z.stop, tp=z.tp2,
                                   qty=z.qty, leverage=z.leverage, margin=z.notional / z.leverage, created_at=now,
                                   version=s.version, strategy=f"scalp {sig.setup}", risk_usd=z.risk_usd, risk_pct=risk,
                                   plan={"tp1": z.tp1, "tp2": z.tp2, "net_rr": z.net_rr, "liq": z.liq, "why": sig.why,
                                         "placed_t": c1[-1]["t"], "qty_open": z.qty, "realized": 0.0, "tp1_done": False,
                                         "mfe": 0.0})
        self.last_reason = f"order #{tid} working"
        return [f"#{tid} {sig.setup}: BTC {sig.side.upper()} post-only {sig.entry:,.1f} | SL {z.stop:,.1f} "
                f"({abs(sig.entry - z.stop) / sig.entry * 100:.2f}%) | TP1 {z.tp1:,.1f} / TP2 {z.tp2:,.1f} | "
                f"{z.leverage}x isolated, risk ${z.risk_usd:.2f} | {sig.why}"]

    def _ask_veto(self, sig: Signal, z: Size, c1: list[dict], tf: int) -> dict | None:
        """Claude reviews the entry (advisory). Any error or slow answer = None = the deterministic decision stands."""
        import concurrent.futures as cf
        c5 = agg(c1, 5)[-24:]
        payload = {"coin": self.s.coin, "setup": sig.setup, "side": sig.side, "why": sig.why, "entry": sig.entry,
                   "stop": z.stop, "tp1": z.tp1, "tp2": z.tp2, "net_rr_tp2": round(z.net_rr, 2), "signal_tf_min": tf,
                   "last_5m_candles": [{k: round(v, 1) for k, v in x.items() if k != "t"} for x in c5],
                   "regime": regime(c1, self.s)[0], "atr_5m_pct": atr_pct(agg(c1, 5)),
                   "recent_results_r": [t["result_r"] for t in self.store.closed(self._since())[-10:]]}
        ex = cf.ThreadPoolExecutor(max_workers=1)
        try:
            return ex.submit(self.veto, payload).result(timeout=8)
        except Exception as e:  # noqa: BLE001
            log.info("claude veto unavailable (%s) - deterministic decision stands", str(e)[:80])
            return None
        finally:
            ex.shutdown(wait=False)

    def _manage(self, k: dict, c1: list[dict], now: float) -> list[str]:
        import json
        s, msgs = self.s, []
        for t in self.store.trades("pending"):
            plan = json.loads(t["plan"])
            if k["t"] <= plan["placed_t"]:
                continue
            through = k["l"] < t["entry"] if t["side"] == "long" else k["h"] > t["entry"]
            if through:
                self.store.update_trade(t["id"], status="open", filled_at=k["t"] / 1000 + 30, fill_px=t["entry"])
                msgs.append(f"#{t['id']} BTC {t['side']} filled at {t['entry']:,.1f}")
                # the fill candle can still stop us out (its range may include the stop); targets wait for the next candle
                hit = k["l"] <= t["sl"] if t["side"] == "long" else k["h"] >= t["sl"]
                if hit:
                    msgs += self._close(self.store.trade(t["id"]), t["sl"], "sl", k, taker=True)
            elif (k["t"] - plan["placed_t"]) / M1 >= s.entry_timeout_min:
                self.store.update_trade(t["id"], status="cancelled", closed_at=k["t"] / 1000 + 60, close_reason="not filled")
                msgs.append(f"#{t['id']} entry not traded through in {s.entry_timeout_min:g} min - cancelled")
        for t in self.store.trades("open"):
            plan = json.loads(t["plan"])
            if k["t"] / 1000 < (t["filled_at"] or 0):
                continue
            sgn = 1 if t["side"] == "long" else -1
            r_px = abs(t["entry"] - (plan.get("orig_sl") or t["sl"]))
            fav = (k["h"] - t["entry"]) if sgn > 0 else (t["entry"] - k["l"])
            plan["mfe"] = max(plan["mfe"], fav / r_px if r_px else 0)
            hit_sl = k["l"] <= t["sl"] if sgn > 0 else k["h"] >= t["sl"]
            if hit_sl:  # pessimistic: a candle touching both counts as the stop
                msgs += self._close(t, t["sl"], "be" if plan["tp1_done"] else "sl", k, taker=True, plan=plan)
                continue
            if s.tp1_r > 0 and not plan["tp1_done"] and ((k["h"] >= plan["tp1"]) if sgn > 0 else (k["l"] <= plan["tp1"])):
                half = math.floor(t["qty"] / 2 * 1e5) / 1e5
                plan["realized"] += half * abs(plan["tp1"] - t["entry"]) - half * plan["tp1"] * s.maker_pct / 100
                plan["qty_open"] = t["qty"] - half
                plan["tp1_done"], plan["orig_sl"] = True, t["sl"]
                be = t["entry"] * (1 + sgn * (s.maker_pct + s.taker_pct + s.slippage_pct) / 100)
                self.store.update_trade(t["id"], sl=be, plan=plan)
                msgs.append(f"#{t['id']} TP1 hit at {plan['tp1']:,.1f}: half off, stop to break-even {be:,.1f}")
                continue
            if (k["h"] >= t["tp"]) if sgn > 0 else (k["l"] <= t["tp"]):
                msgs += self._close(t, t["tp"], "tp", k, taker=False, plan=plan)
                continue
            mins = (k["t"] / 1000 + 60 - t["filled_at"]) / 60
            in_s, end = self.in_session(k["t"] / 1000)
            if not plan["tp1_done"] and mins >= s.time_stop_min and plan["mfe"] < s.time_stop_r:
                msgs += self._close(t, k["c"], "time", k, taker=True, plan=plan)
            elif mins >= s.max_hold_min or not in_s or end - (k["t"] / 1000 + 60) <= 0:
                msgs += self._close(t, k["c"], "session" if not in_s else "time", k, taker=True, plan=plan)
            else:
                self.store.update_trade(t["id"], plan=plan)
        return msgs

    def _close(self, t: dict, px: float, kind: str, k: dict, taker: bool, plan: dict | None = None) -> list[str]:
        import json
        s = self.s
        plan = plan or json.loads(t["plan"])
        qo = plan.get("qty_open", t["qty"])
        sgn = 1 if t["side"] == "long" else -1
        fee_in = t["qty"] * t["entry"] * s.maker_pct / 100
        fee_out = qo * px * ((s.taker_pct + s.slippage_pct) if taker else s.maker_pct) / 100
        pnl = plan["realized"] + sgn * (px - t["entry"]) * qo - fee_in - fee_out
        r = pnl / t["risk_usd"] if t["risk_usd"] else 0.0
        exit_type = ("tp1+" + kind) if plan["tp1_done"] and kind != "tp1" else kind
        closed_at = k["t"] / 1000 + 60
        self.store.update_trade(t["id"], status="closed", exit_px=px, pnl=pnl, result_r=r, exit_type=exit_type,
                                closed_at=closed_at, fees=fee_in + fee_out, plan=plan)
        self.st["cash"] = self.equity() + pnl
        self.st["hwm"] = max(self.st["hwm"], self.st["cash"])
        if (1 - self.st["cash"] / self.st["hwm"]) * 100 >= s.dd_pause_pct:
            self.st["paused"] = True
        rs = [x["result_r"] or 0 for x in self.store.closed(self._since())][-s.loss_streak_cooldown:]
        if len(rs) == s.loss_streak_cooldown and all(x < 0 for x in rs):
            self.st["cooldown_until"] = closed_at + s.cooldown_min * 60
        self._save()
        self.store.record_equity(self.st["cash"])
        return [f"#{t['id']} BTC {t['side']} closed ({exit_type}) at {px:,.1f}: {r:+.2f}R ${pnl:+.2f} | "
                f"equity ${self.st['cash']:.2f} | gate: {self.gate()[1]}"]

    # -- owner commands -------------------------------------------------------
    def status(self) -> str:
        now = time.time()
        rs = [t["result_r"] or 0 for t in self.store.closed(self._since())]
        today = self.closed_today(now)
        ok, _ = self.in_session(now)
        risk, block = self.risk_now(now)
        w = sum(1 for r in rs if r > 0)
        lines = [f"HEVI SCALP {self.s.version} - BTC, paper, separate $100 ledger",
                 f"Equity ${self.equity():.2f} (start ${self.st['start']:.2f}, high ${self.st['hwm']:.2f})",
                 f"All time: {len(rs)} scalps, {w} wins, {sum(rs):+.2f}R | today: {len(today)} scalps, "
                 f"{sum(t['result_r'] or 0 for t in today):+.2f}R",
                 f"Gate: {self.gate()[1]}",
                 f"Session: {'OPEN' if ok else 'closed'} (07-11, 13-17 UTC) | risk {risk:g}% | "
                 + ("blocked: " + "; ".join(block) if block else "ready"),
                 f"Last check: {self.last_reason}"]
        for t in self.store.trades("open") + self.store.trades("pending"):
            lines.append(f"#{t['id']} {t['status']} BTC {t['side']} @ {t['entry']:,.1f} SL {t['sl']:,.1f} TP {t['tp']:,.1f}")
        return "\n".join(lines)

    def halt(self, on: bool) -> str:
        self.st["halted"] = on
        if not on:
            self.st["paused"] = False
        self._save()
        return "Scalp: no new entries (open trades keep their stops)." if on else "Scalp resumed."

    def flatten(self) -> str:
        n = 0
        c1 = self.mkt.candles(self.s.coin, "1m", 5)
        k = c1[-1]
        for t in self.store.trades("pending"):
            self.store.update_trade(t["id"], status="cancelled", closed_at=time.time(), close_reason="owner /scalp flat")
            n += 1
        out = []
        for t in self.store.trades("open"):
            out += self._close(t, k["c"], "manual", k, taker=True)
            n += 1
        self.st["halted"] = True
        self._save()
        return "\n".join(out + [f"Scalp flat: {n} order(s)/position(s) closed; halted until /scalp resume."])


def scorecard(rs: list[float], seed: int = 7, n_boot: int = 2000) -> dict:
    """Trades, win rate, expectancy, profit factor, total R and a bootstrap 90% interval for the expectancy."""
    import random
    n = len(rs)
    if not n:
        return {"n": 0}
    w, l = sum(r for r in rs if r > 0), -sum(r for r in rs if r <= 0)
    rnd, means = random.Random(seed), []
    for _ in range(n_boot):
        means.append(sum(rnd.choice(rs) for _ in range(n)) / n)
    means.sort()
    return {"n": n, "win": sum(r > 0 for r in rs) / n, "exp": sum(rs) / n, "pf": w / l if l else float("inf"),
            "total": sum(rs), "lo": means[int(0.05 * n_boot)], "hi": means[int(0.95 * n_boot) - 1]}
