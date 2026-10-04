"""Pure indicator / level math. Candles are dicts with keys t,o,h,l,c,v (floats; t in ms)."""

from __future__ import annotations

import math
from statistics import mean, pstdev


def bollinger(candles: list[dict], period: int = 20, mult: float = 2.0) -> dict | None:
    if len(candles) < period:
        return None
    closes = [c["c"] for c in candles[-period:]]
    mid = mean(closes)
    sd = pstdev(closes)
    return {"lower": mid - mult * sd, "mid": mid, "upper": mid + mult * sd}


def fib_pivots(high: float, low: float, close: float) -> dict:
    """Fibonacci pivot points from a prior period's H/L/C."""
    p = (high + low + close) / 3
    r = high - low
    return {
        "S3": p - r,
        "S2": p - 0.618 * r,
        "S1": p - 0.382 * r,
        "P": p,
        "R1": p + 0.382 * r,
        "R2": p + 0.618 * r,
        "R3": p + r,
    }


def vwap(candles: list[dict]) -> dict | None:
    """Volume-weighted average price over the given candles, with +/-1 sigma bands."""
    vol = sum(c["v"] for c in candles)
    if not candles or vol <= 0:
        return None
    tps = [(c["h"] + c["l"] + c["c"]) / 3 for c in candles]
    vw = sum(tp * c["v"] for tp, c in zip(tps, candles)) / vol
    var = sum(c["v"] * (tp - vw) ** 2 for tp, c in zip(tps, candles)) / vol
    sd = math.sqrt(var)
    return {"vwap": vw, "upper": vw + sd, "lower": vw - sd}


def volume_profile(candles: list[dict], bins: int = 50, value_area: float = 0.70) -> dict | None:
    """Point of control + value area high/low. Each candle's volume is spread evenly across its range."""
    if not candles:
        return None
    lo = min(c["l"] for c in candles)
    hi = max(c["h"] for c in candles)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    hist = [0.0] * bins
    for c in candles:
        a = int((c["l"] - lo) / step)
        b = int((c["h"] - lo) / step)
        a, b = max(0, min(a, bins - 1)), max(0, min(b, bins - 1))
        share = c["v"] / (b - a + 1)
        for i in range(a, b + 1):
            hist[i] += share
    total = sum(hist)
    if total <= 0:
        return None
    poc_i = max(range(bins), key=lambda i: hist[i])
    lo_i = hi_i = poc_i
    acc = hist[poc_i]
    while acc < value_area * total and (lo_i > 0 or hi_i < bins - 1):
        down = hist[lo_i - 1] if lo_i > 0 else -1
        up = hist[hi_i + 1] if hi_i < bins - 1 else -1
        if up >= down:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    center = lambda i: lo + (i + 0.5) * step  # noqa: E731
    return {"poc": center(poc_i), "vah": lo + (hi_i + 1) * step, "val": lo + lo_i * step}


def atr(candles: list[dict], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for prev, c in zip(candles[-period - 1 : -1], candles[-period:]):
        trs.append(max(c["h"] - c["l"], abs(c["h"] - prev["c"]), abs(c["l"] - prev["c"])))
    return mean(trs)


def swing_points(candles: list[dict], left: int = 2, right: int = 2) -> dict:
    """Fractal swing highs/lows (confirmed only)."""
    highs, lows = [], []
    for i in range(left, len(candles) - right):
        h = candles[i]["h"]
        l = candles[i]["l"]
        if all(h > candles[j]["h"] for j in range(i - left, i + right + 1) if j != i):
            highs.append(h)
        if all(l < candles[j]["l"] for j in range(i - left, i + right + 1) if j != i):
            lows.append(l)
    return {"highs": highs, "lows": lows}


def realized_vol_rank(hourly: list[dict], window: int = 24) -> dict | None:
    """Percentile rank of the latest rolling realized volatility within the series.

    Used as a free stand-in for IV Rank on coins without an options market.
    """
    if len(hourly) < window * 3:
        return None
    rets = [math.log(b["c"] / a["c"]) for a, b in zip(hourly[:-1], hourly[1:]) if a["c"] > 0 and b["c"] > 0]
    vols = []
    for i in range(window, len(rets) + 1):
        vols.append(pstdev(rets[i - window : i]))
    cur = vols[-1]
    rank = 100.0 * sum(1 for v in vols if v <= cur) / len(vols)
    annual = cur * math.sqrt(24 * 365) * 100
    return {"rank": rank, "label": rank_label(rank), "realized_vol_annual_pct": annual}


def rank_label(rank: float) -> str:
    if rank >= 90:
        return "EXTREME"
    if rank >= 75:
        return "HIGH"
    if rank >= 25:
        return "NORMAL"
    return "LOW"


def percentile_rank(series: list[float], value: float) -> float:
    if not series:
        return 50.0
    return 100.0 * sum(1 for v in series if v <= value) / len(series)


def bearish_rejection(candle: dict, level: float, tol: float = 0.002) -> bool:
    """Candle wicked into/through `level` but closed below it, with a dominant upper wick."""
    rng = candle["h"] - candle["l"]
    if rng <= 0:
        return False
    upper_wick = candle["h"] - max(candle["o"], candle["c"])
    return candle["h"] >= level * (1 - tol) and candle["c"] < level and upper_wick / rng >= 0.5


def bullish_rejection(candle: dict, level: float, tol: float = 0.002) -> bool:
    rng = candle["h"] - candle["l"]
    if rng <= 0:
        return False
    lower_wick = min(candle["o"], candle["c"]) - candle["l"]
    return candle["l"] <= level * (1 + tol) and candle["c"] > level and lower_wick / rng >= 0.5


def aggregate(candles: list[dict], n: int) -> list[dict]:
    """Merge every n consecutive candles (oldest first) into one."""
    out = []
    for i in range(0, len(candles) - n + 1, n):
        chunk = candles[i : i + n]
        out.append(
            {
                "t": chunk[0]["t"],
                "o": chunk[0]["o"],
                "h": max(c["h"] for c in chunk),
                "l": min(c["l"] for c in chunk),
                "c": chunk[-1]["c"],
                "v": sum(c["v"] for c in chunk),
            }
        )
    return out
