"""Scalp backtest: replay the real ScalpEngine minute by minute.

Same code, same fills (trade-through, stop-first), same fees as the paper scalper, in a throwaway ledger.
History: Hyperliquid serves only its last 5000 1m candles (~3.5 days), so older days come from Binance's public
archive (data.binance.vision, BTCUSDT perpetual, daily files; no key, cached on the volume). Binance's BTCUSDT and
Hyperliquid's BTC trade within a few dollars of each other: fine for testing rules, not identical fills."""

from __future__ import annotations

import bisect
import io
import os
import tempfile
import time
import zipfile
from collections import Counter

import requests

from .scalp import ScalpEngine, scorecard

M1 = 60_000


class _ReplayMarket:
    def __init__(self, candles, tiers, szd):
        self.c, self.tiers, self.szd, self.t = candles, tiers, szd, 0
        self.ts = [x["t"] for x in candles]

    def candles(self, coin, iv, n):
        i = bisect.bisect_right(self.ts, self.t - M1)       # candles completed by self.t
        return self.c[max(0, i - n):i]

    def ctxs(self):
        return {"BTC": {"margin_tiers": self.tiers, "sz_decimals": self.szd}}

    def macro_calendar(self, names):
        return [], None


BINANCE = "https://data.binance.vision/data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{day}.zip"


def binance_day(day: str, cache_dir: str | None, sym: str = "BTCUSDT") -> list[dict]:
    """One UTC day of 1m candles from Binance's public archive (cached as the original zip)."""
    path = os.path.join(cache_dir, f"{sym}-1m-{day}.zip") if cache_dir else None
    if path and os.path.exists(path):
        raw = open(path, "rb").read()
    else:
        r = requests.get(BINANCE.format(sym=sym, day=day), timeout=30)
        if r.status_code != 200:
            return []
        raw = r.content
        if path:
            os.makedirs(cache_dir, exist_ok=True)
            open(path, "wb").write(raw)
    z = zipfile.ZipFile(io.BytesIO(raw))
    out = []
    for ln in z.read(z.namelist()[0]).decode().splitlines():
        p = ln.split(",")
        if not p or not p[0].isdigit():
            continue
        out.append({"t": int(p[0]), "o": float(p[1]), "h": float(p[2]), "l": float(p[3]), "c": float(p[4]), "v": float(p[5])})
    return out


def fetch_hl(info, coin="BTC") -> list[dict]:
    now = int(time.time() * 1000)
    raw = info.candles_snapshot(coin, "1m", now - 5000 * M1, now)
    return [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]),
             "v": float(c["v"])} for c in raw]


def fetch(info, coin="BTC", days: int = 3, cache_dir: str | None = None) -> tuple[list[dict], str]:
    """1m candles for the last `days` days: Binance archive for the older part, Hyperliquid for the newest."""
    hl = fetch_hl(info, coin)
    if days <= 3 or coin != "BTC":
        return hl, "Hyperliquid"
    first = hl[0]["t"] if hl else int(time.time() * 1000)
    old: list[dict] = []
    for k in range(days + 1, 0, -1):
        day = time.strftime("%Y-%m-%d", time.gmtime(first / 1000 - k * 86400))
        old += [c for c in binance_day(day, cache_dir) if c["t"] < first]
    old = [c for c in old if c["t"] >= first - days * 86_400_000]
    return old + hl, ("Binance BTCUSDT archive + Hyperliquid" if old else "Hyperliquid")


def replay(candles: list[dict], tiers, szd: int, cfg, P, s=None, warm: int = 1400) -> list[dict]:
    """Closed trades of one rules variant over the candles (the first `warm` minutes only build indicators)."""
    from dataclasses import replace
    m = _ReplayMarket(candles, tiers, szd)
    cfg2 = replace(cfg, data_dir=tempfile.mkdtemp(prefix="scalp-bt-"))
    # replays measure the rules, so the drawdown pause (which waits for a human /scalp resume) is off here; the
    # daily stop, loss streak cooldown and trade cap stay on
    eng = ScalpEngine(cfg2, m, P, s=replace(s or P.scalp, claude_veto=False, dd_pause_pct=1e9, dd_half_pct=1e9), name="bt")
    eng.st["last_t"] = candles[warm]["t"]
    eng.st["reset_at"] = candles[warm]["t"] / 1000
    for c in candles[warm + 1:]:
        m.t = c["t"] + M1 + 1
        eng.tick(m.t / 1000)
    return eng.store.closed(0)


def line(name: str, trades: list[dict], days: float) -> str:
    c = scorecard([t["result_r"] for t in trades])
    if not c["n"]:
        return f"{name}: no trades"
    pf = "inf" if c["pf"] == float("inf") else f"{c['pf']:.2f}"
    return (f"{name}: {c['n']} trades ({c['n'] / days:.1f}/day) | win {c['win'] * 100:.0f}% | {c['exp']:+.2f}R/trade "
            f"[{c['lo']:+.2f}, {c['hi']:+.2f}] | PF {pf} | total {c['total']:+.1f}R | ${sum(t['pnl'] or 0 for t in trades):+.2f}")


def run(info, ctxs: dict, cfg, P, arms: bool = False, days: int | None = None) -> str:
    days = max(3, min(90, days or (14 if arms else 7)))
    candles, src = fetch(info, P.scalp.coin, days, os.path.join(cfg.data_dir, "candles"))
    d = ctxs[P.scalp.coin]
    span = (candles[-1]["t"] - candles[1400]["t"]) / 86_400_000
    day = lambda ms: time.strftime("%d %b %H:%M", time.gmtime(ms / 1000))  # noqa: E731
    out = [f"Scalp backtest - {P.scalp.version}, BTC 1m, {day(candles[1400]['t'])} to {day(candles[-1]['t'])} UTC "
           f"({span:.1f} days; {src})",
           "Same engine, fills and fees as the paper scalper. Claude veto and the drawdown pause are off in replays.", ""]
    live = replay(candles, d["margin_tiers"], d["sz_decimals"], cfg, P)
    out.append(line("A live rules", live, span))
    if live:
        out.append("  exits: " + ", ".join(f"{k} {v}" for k, v in Counter(t["exit_type"] for t in live).most_common()))
    if arms:
        from .arms import ARMS
        for name, (desc, s) in ARMS.items():
            if s.claude_veto:
                continue
            out.append(line(name, replay(candles, d["margin_tiers"], d["sz_decimals"], cfg, P, s=s), span))
        out.append("(I_claude needs live Claude calls, so it is only measured forward on /arms.)")
    out.append("")
    out.append("Ranges are 90% intervals for R/trade: trust a version only when its low end is above 0.")
    return "\n".join(out)
