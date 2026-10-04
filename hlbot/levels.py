"""Support/resistance maps (Module 3), confluence entries (Module 5) and SL/TP placement (Module 6)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import indicators as ind

DAY_MS = 86_400_000


@dataclass
class Level:
    price: float
    method: str  # BB | FIB | VWAP | VP | SWING
    tf: str  # 5m | 4h | 1d
    name: str

    def label(self) -> str:
        return f"{self.tf} {self.method} {self.name} {fmt(self.price)}"


def fmt(px: float) -> str:
    if px >= 1000:
        return f"{px:,.1f}"
    if px >= 1:
        return f"{px:.4f}".rstrip("0").rstrip(".")
    return f"{px:.6g}"


def _bb(c, tf) -> list[Level]:
    b = ind.bollinger(c)
    if not b:
        return []
    return [Level(b["lower"], "BB", tf, "lower"), Level(b["mid"], "BB", tf, "mid"), Level(b["upper"], "BB", tf, "upper")]


def _fib(h, l, cl, tf) -> list[Level]:
    return [Level(v, "FIB", tf, k) for k, v in ind.fib_pivots(h, l, cl).items()]


def _vwap(c, tf) -> list[Level]:
    v = ind.vwap(c)
    if not v:
        return []
    return [Level(v["vwap"], "VWAP", tf, "vwap"), Level(v["upper"], "VWAP", tf, "+1sd"), Level(v["lower"], "VWAP", tf, "-1sd")]


def _vp(c, tf) -> list[Level]:
    v = ind.volume_profile(c)
    if not v:
        return []
    return [Level(v["poc"], "VP", tf, "POC"), Level(v["vah"], "VP", tf, "VAH"), Level(v["val"], "VP", tf, "VAL")]


def build_levels(c5m: list[dict], c4h: list[dict], c1d: list[dict]) -> dict[str, list[Level]]:
    """All four methods on 5m, 4h and 1d. The last candle of each list is assumed to be still forming."""
    out: dict[str, list[Level]] = {"5m": [], "4h": [], "1d": []}
    done_d = c1d[:-1] if len(c1d) > 1 else c1d

    # 5m: intraday levels
    out["5m"] += _bb(c5m, "5m")
    if done_d:
        y = done_d[-1]
        out["5m"] += _fib(y["h"], y["l"], y["c"], "5m")
    if c5m:
        day_start = (c5m[-1]["t"] // DAY_MS) * DAY_MS
        session = [c for c in c5m if c["t"] >= day_start] or c5m[-12:]
        out["5m"] += _vwap(session, "5m")
    out["5m"] += _vp(c5m[-288:], "5m")

    # 4h: swing structure (weekly pivots)
    out["4h"] += _bb(c4h, "4h")
    if len(done_d) >= 7:
        wk = done_d[-7:]
        out["4h"] += _fib(max(c["h"] for c in wk), min(c["l"] for c in wk), wk[-1]["c"], "4h")
    out["4h"] += _vwap(c4h[-42:], "4h")
    out["4h"] += _vp(c4h[-90:], "4h")
    sw = ind.swing_points(c4h[-90:])
    out["4h"] += [Level(p, "SWING", "4h", "low") for p in sw["lows"][-6:]]
    out["4h"] += [Level(p, "SWING", "4h", "high") for p in sw["highs"][-6:]]

    # 1d: higher-timeframe (monthly pivots)
    out["1d"] += _bb(c1d, "1d")
    if len(done_d) >= 20:
        mo = done_d[-30:]
        out["1d"] += _fib(max(c["h"] for c in mo), min(c["l"] for c in mo), mo[-1]["c"], "1d")
    out["1d"] += _vwap(c1d[-30:], "1d")
    out["1d"] += _vp(c1d[-90:], "1d")
    return out


@dataclass
class Confluence:
    price: float
    methods: list[str]
    members: list[Level]


def find_confluence(levels: list[Level], price: float, side: str, max_dist_pct: float, tol_pct: float) -> Confluence | None:
    """Best cluster of 5m levels on the entry side of price (below for longs, above for shorts).

    Ranked by number of distinct methods agreeing, then by proximity. Needs >= 2 methods.
    """
    if side == "long":
        cands = [l for l in levels if price * (1 - max_dist_pct / 100) <= l.price <= price]
    else:
        cands = [l for l in levels if price <= l.price <= price * (1 + max_dist_pct / 100)]
    cands.sort(key=lambda l: l.price)
    best: Confluence | None = None
    for anchor in cands:
        members = [l for l in cands if abs(l.price - anchor.price) / anchor.price * 100 <= tol_pct]
        methods = sorted({m.method for m in members})
        if len(methods) < 2:
            continue
        px = sum(m.price for m in members) / len(members)
        conf = Confluence(px, methods, members)
        if best is None or (len(methods), -abs(price - px)) > (len(best.methods), -abs(price - best.price)):
            best = conf
    return best


@dataclass
class Setup:
    coin: str
    side: str
    entry: float
    sl: float
    tp: float
    sl_pct: float
    rr: float
    entry_methods: list[str]
    sl_anchor: str
    tp_anchor: str
    obstacles: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


SL_BUFFER = 0.0015
TP_BUFFER = 0.001


def plan_setup(
    coin: str,
    side: str,
    price: float,
    levels: dict[str, list[Level]],
    *,
    min_sl_pct: float,
    max_sl_pct: float,
    min_rr: float,
    max_entry_dist_pct: float,
    tol_pct: float,
) -> tuple[Setup | None, str]:
    """Entry at 5m confluence, SL anchored to 4h structure (floored at min_sl_pct), TP at >= min_rr."""
    conf = find_confluence(levels["5m"], price, side, max_entry_dist_pct, tol_pct)
    if not conf:
        return None, "no 5m confluence (>=2 methods) within reach"
    entry = conf.price
    sgn = 1 if side == "long" else -1
    notes: list[str] = []

    # --- stop loss: nearest 4h structure beyond entry, widened to the floor -----------
    struct = [l for l in levels["4h"] if sgn * (entry - l.price) / entry * 100 > 0.2]
    struct.sort(key=lambda l: sgn * (entry - l.price))  # nearest first
    floor_sl = entry * (1 - sgn * min_sl_pct / 100)
    if struct:
        anchor = struct[0]
        sl_struct = anchor.price * (1 - sgn * SL_BUFFER)
        if sgn * (entry - sl_struct) >= sgn * (entry - floor_sl):
            sl, sl_anchor = sl_struct, anchor.label()
        else:
            sl, sl_anchor = floor_sl, f"{min_sl_pct}% floor (4h {anchor.method} {anchor.name} too close)"
            notes.append("4h structure closer than SL floor -> widened")
    else:
        sl, sl_anchor = floor_sl, f"{min_sl_pct}% floor (no 4h structure found)"
    sl_pct = abs(entry - sl) / entry * 100
    if sl_pct > max_sl_pct:
        return None, f"4h structure SL {sl_pct:.2f}% wider than max {max_sl_pct}%"

    # --- take profit: first 4h/1d level at or beyond min_rr ------------------------
    risk = abs(entry - sl)
    need = entry + sgn * min_rr * risk
    res = [l for l in levels["4h"] + levels["1d"] if sgn * (l.price - entry) > 0]
    beyond = sorted([l for l in res if sgn * (l.price - need) >= 0], key=lambda l: sgn * (l.price - entry))
    if beyond:
        tp = beyond[0].price * (1 - sgn * TP_BUFFER)
        if sgn * (tp - need) < 0:
            tp = need
        tp_anchor = beyond[0].label()
    else:
        tp, tp_anchor = need, f"{min_rr}R (no mapped level beyond)"
    obstacles = sorted(
        {f"{l.tf} {l.method} {l.name} {fmt(l.price)}" for l in res if sgn * (l.price - entry * (1 + sgn * 0.003)) > 0 and sgn * (tp - l.price) > 0}
    )
    rr = abs(tp - entry) / risk
    return (
        Setup(coin, side, entry, sl, tp, sl_pct, rr, conf.methods, sl_anchor, tp_anchor, obstacles, notes),
        "ok",
    )


def nearest(levels: list[Level], price: float, above: bool) -> Level | None:
    xs = [l for l in levels if (l.price > price if above else l.price < price)]
    if not xs:
        return None
    return min(xs, key=lambda l: abs(l.price - price))
