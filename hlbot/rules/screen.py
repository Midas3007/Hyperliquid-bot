"""Module 2 - screener: two sweeps, then automatic skips (no analysis needed), then cooldowns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..protocol import Screener
from .checks import Check, na_check


@dataclass(frozen=True)
class Row:
    coin: str
    volume_usd: float
    buy_sell: float | None      # taker buy/sell notional ratio (N/A until enough trade history)
    day_change_pct: float | None  # from the 00:00 UTC open


def sweeps(rows: list[Row], s: Screener, skip: Callable[[str], str | None]) -> list[tuple[str, str]]:
    """Sweep 1: highest buy/sell ratio, volume > $50M. Sweep 2: strongest momentum (UTC-day change), volume > $100M.
    `skip(coin)` returns a reason for coins that are auto-skipped; the sweep walks down the ranking past them.
    Returns [(coin, tag)] without duplicates, sweep 1 first."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def walk(pool: list[Row], n: int, tag: str) -> None:
        taken = 0
        for r in pool:
            if taken >= n:
                break
            if r.coin in seen or skip(r.coin):
                continue
            seen.add(r.coin)
            out.append((r.coin, tag))
            taken += 1

    s1 = sorted((r for r in rows if r.volume_usd > s.sweep1_min_volume_usd and r.buy_sell is not None),
                key=lambda r: r.buy_sell, reverse=True)
    s2 = sorted((r for r in rows if r.volume_usd > s.sweep2_min_volume_usd and r.day_change_pct is not None
                 and r.day_change_pct > 0), key=lambda r: r.day_change_pct, reverse=True)
    walk(s1, s.sweep1_top_n, "sweep 1: buy/sell")
    walk(s2, s.sweep2_top_n, "sweep 2: momentum")
    return out


def cooldown_until(coin: str, sl_exits: list[tuple[str, float]], now: float, s: Screener) -> tuple[float, str] | None:
    """SL on a coin = off-limits 24h. 2 SLs on the same coin within 30 days = off-limits 14 days.
    `sl_exits`: [(coin, closed_at)] for every SL exit. Same rule for every coin."""
    times = sorted(t for c, t in sl_exits if c == coin and t <= now)
    if not times:
        return None
    best: tuple[float, str] | None = None
    last = times[-1]
    if last + s.cooldown_after_sl_h * 3600 > now:
        best = (last + s.cooldown_after_sl_h * 3600, f"SL {((now - last) / 3600):.0f}h ago (24h cooldown)")
    recent = [t for t in times if t >= now - s.cooldown_repeat_window_d * 86400]
    if len(recent) >= s.cooldown_repeat_count:
        until = recent[-1] + s.cooldown_repeat_h * 3600
        if until > now and (best is None or until > best[0]):
            best = (until, f"{len(recent)} SLs in {s.cooldown_repeat_window_d:g} days (14-day cooldown)")
    return best


def auto_skips(
    s: Screener,
    *,
    coin: str,
    day_change_pct: float | None,
    unlock: tuple[bool, str] | None,
    vol_label: str | None,
    whale_ls: float | None,
    whale_is_true: bool,
    cooldown: tuple[float, str] | None,
) -> list[Check]:
    """Failing auto-skip checks for one coin (empty list = passes to the deep dive).
    unlock: (inside window?, detail) or None when no unlock data covers the window (missing = FAIL)."""
    out: list[Check] = []
    base = coin[1:].upper() if coin.startswith("k") and coin[1:].isupper() else coin.upper()  # kPEPE -> PEPE
    if base in s.stablecoins:
        out.append(Check("M2", "stablecoin", False, f"{coin} is a stablecoin"))
    if day_change_pct is None:
        out.append(na_check("M2", "daily change", "no UTC daily open"))
    elif day_change_pct < -s.max_day_drop_pct:
        out.append(Check("M2", "down > 8%", False, f"{day_change_pct:+.1f}% on the UTC day"))
    if unlock is None:
        out.append(na_check("M2", "token unlock", "no unlock data covers the hold window + 24h (calendar.toml)"))
    elif unlock[0]:
        out.append(Check("M2", "token unlock", False, unlock[1]))
    if vol_label == "EXTREME":
        out.append(Check("M2", "EXTREME volatility", False, "volatility >= 85th percentile", hard=True))
    if whale_is_true and whale_ls is not None and whale_ls < s.whale_autoskip_below:
        out.append(Check("M2", "whale L/S < 1:1", False, f"whale L/S {whale_ls:.2f}"))
    if cooldown:
        out.append(Check("M2", "cooldown", False, cooldown[1]))
    return out


def unlock_in_window(events: list[tuple[str, float, float]], covered_until: float | None, coin: str,
                     start: float, end: float, s: Screener) -> tuple[bool, str] | None:
    """events: [(coin, ts, supply_pct)]. Window = hold + 24h. None if the calendar doesn't cover it."""
    stop = end + s.unlock_extra_h * 3600
    if covered_until is None or covered_until < stop:
        return None
    hits = [(t, p) for c, t, p in events if c == coin.upper() and start <= t <= stop and p >= s.unlock_min_supply_pct]
    if hits:
        t, p = hits[0]
        return True, f"unlock of {p:g}% of supply inside hold + 24h"
    return False, "no unlock >= 1% in hold + 24h"
