"""Scalp backtest: replay the real ScalpEngine minute by minute over Hyperliquid's last 5000 1m candles (~3.5 days).

Same code, same fills (trade-through, stop-first), same fees as the paper scalper, in a throwaway ledger. Hyperliquid
serves only the most recent 5000 candles per interval, so this is a short window: read it as a smoke test of the rules,
not as proof of an edge."""

from __future__ import annotations

import tempfile
import time
from collections import Counter

from .scalp import ScalpEngine, scorecard

M1 = 60_000


class _ReplayMarket:
    def __init__(self, candles, tiers, szd):
        self.c, self.tiers, self.szd, self.t = candles, tiers, szd, 0

    def candles(self, coin, iv, n):
        out = [x for x in self.c if x["t"] + M1 <= self.t]
        return out[-n:]

    def ctxs(self):
        return {"BTC": {"margin_tiers": self.tiers, "sz_decimals": self.szd}}

    def macro_calendar(self, names):
        return [], None


def fetch(info, coin="BTC") -> list[dict]:
    now = int(time.time() * 1000)
    raw = info.candles_snapshot(coin, "1m", now - 5000 * M1, now)
    return [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]),
             "v": float(c["v"])} for c in raw]


def replay(candles: list[dict], tiers, szd: int, cfg, P, s=None, warm: int = 1400) -> list[dict]:
    """Closed trades of one rules variant over the candles (the first `warm` minutes only build indicators)."""
    from dataclasses import replace
    m = _ReplayMarket(candles, tiers, szd)
    cfg2 = replace(cfg, data_dir=tempfile.mkdtemp(prefix="scalp-bt-"))
    eng = ScalpEngine(cfg2, m, P, s=replace(s or P.scalp, claude_veto=False), name="bt")
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


def run(info, ctxs: dict, cfg, P, arms: bool = False) -> str:
    candles = fetch(info, P.scalp.coin)
    d = ctxs[P.scalp.coin]
    span = (candles[-1]["t"] - candles[1400]["t"]) / 86_400_000
    day = lambda ms: time.strftime("%d %b %H:%M", time.gmtime(ms / 1000))  # noqa: E731
    out = [f"Scalp backtest - {P.scalp.version}, BTC 1m, {day(candles[1400]['t'])} to {day(candles[-1]['t'])} UTC "
           f"({span:.1f} days; Hyperliquid keeps only the last 5000 1m candles)",
           "Same engine, fills and fees as the paper scalper. Claude veto is off in replays. Short window: a smoke test, not proof.",
           ""]
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
    return "\n".join(out)
