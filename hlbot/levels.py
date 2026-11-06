"""Support/resistance maps on 5m / 4H / 1D with all four methods (Bollinger, Fibonacci, VWAP, Volume Profile),
plus 4H swing points. Entry, SL and TP placement live in hlbot/rules/bracket.py."""

from __future__ import annotations

from dataclasses import dataclass

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


def nearest(levels: list[Level], price: float, above: bool) -> Level | None:
    xs = [l for l in levels if (l.price > price if above else l.price < price)]
    if not xs:
        return None
    return min(xs, key=lambda l: abs(l.price - price))
