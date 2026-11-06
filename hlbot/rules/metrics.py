"""Module 3 measurements computed from candles and snapshots. Candles: dicts t,o,h,l,c,v (t in ms, oldest first).

All functions return None when the input can't support the number (missing data is never estimated).
"""

from __future__ import annotations

import math
from statistics import mean, pstdev

DAY_MS = 86_400_000


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1) * 100


def utc_day_open(daily: list[dict], now_ms: int) -> float | None:
    """Open of the current UTC day's candle (Hyperliquid daily candles start at 00:00 UTC)."""
    start = now_ms // DAY_MS * DAY_MS
    today = [c for c in daily if c["t"] == start]
    return today[-1]["o"] if today else None


def completed(candles: list[dict], interval_ms: int, now_ms: int) -> list[dict]:
    """Drop the still-forming candle."""
    return [c for c in candles if c["t"] + interval_ms <= now_ms]


def adr_pct(daily_completed: list[dict], days: int) -> float | None:
    """Average daily range over the last `days` completed UTC days, as % of each day's open."""
    rows = [c for c in daily_completed[-days:] if c["o"] > 0]
    if len(rows) < days:
        return None
    return mean((c["h"] - c["l"]) / c["o"] * 100 for c in rows)


def atr(candles: list[dict], period: int) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"]))
           for p, c in zip(candles[-period - 1:-1], candles[-period:])]
    return mean(trs)


def atr_pct(candles: list[dict], period: int, price: float) -> float | None:
    a = atr(candles, period)
    return a / price * 100 if a and price > 0 else None


def rv_percentile(hourly_completed: list[dict], window_h: int, lookback_d: int) -> tuple[float, float] | None:
    """Realized vol (annualized %, from hourly log returns over `window_h`) and its percentile among every
    rolling window in the last `lookback_d` days. 0 = missing data, not low volatility."""
    need = lookback_d * 24 + window_h
    if len(hourly_completed) < need:
        return None
    closes = [c["c"] for c in hourly_completed[-need:]]
    if any(x <= 0 for x in closes):
        return None
    rets = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:])]
    vols = [pstdev(rets[i - window_h:i]) for i in range(window_h, len(rets) + 1)]
    cur = vols[-1]
    if cur == 0:
        return None
    pct = 100.0 * sum(1 for v in vols if v <= cur) / len(vols)
    return cur * math.sqrt(24 * 365) * 100, pct


def vol_label(percentile: float | None, low_below: float, high_at: float, extreme_at: float) -> str | None:
    if percentile is None:
        return None
    if percentile >= extreme_at:
        return "EXTREME"
    if percentile >= high_at:
        return "HIGH"
    if percentile < low_below:
        return "LOW"
    return "NORMAL"


def funding_8h(funding_1h_pct: float | None, basis_h: float = 8) -> float | None:
    """Hyperliquid pays funding every hour. Express it on the protocol's 8h basis."""
    return None if funding_1h_pct is None else funding_1h_pct * basis_h


def funding_rising(hourly_rates_pct: list[float]) -> bool | None:
    """Latest hourly rate above the average of the 8 before it."""
    if len(hourly_rates_pct) < 9:
        return None
    return hourly_rates_pct[-1] > mean(hourly_rates_pct[-9:-1])


def rejection_wick(candle: dict, level: float, side: str, min_share: float, tol_pct: float) -> bool:
    """Bearish rejection (side='short'): wicked into the level, closed back below, upper wick dominant.
    Bullish rejection (side='long') mirrors it at support."""
    rng = candle["h"] - candle["l"]
    if rng <= 0:
        return False
    tol = tol_pct / 100
    if side == "short":
        wick = candle["h"] - max(candle["o"], candle["c"])
        return candle["h"] >= level * (1 - tol) and candle["c"] < level and wick / rng >= min_share
    wick = min(candle["o"], candle["c"]) - candle["l"]
    return candle["l"] <= level * (1 + tol) and candle["c"] > level and wick / rng >= min_share


def closed_below(candles_completed: list[dict], levels: list[float]) -> float | None:
    """Level that the last completed candle closed below after the candle before closed above it."""
    if len(candles_completed) < 2:
        return None
    prev, last = candles_completed[-2]["c"], candles_completed[-1]["c"]
    broken = [lv for lv in levels if prev >= lv > last]
    return max(broken) if broken else None
