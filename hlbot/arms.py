"""Shadow arms for the scalper (plan Phase 4). Pre-registered in docs/ARMS.md before any results existed.

Each arm is the live scalp protocol with one or two settings changed. It runs on the same 1m candles in its own
paper ledger (DATA_DIR/arms/<name>/), sends no Telegram messages, and is only compared on /arms. The live scalper is
arm A. The winner is chosen by the lower bound of its expectancy interval, never by total R alone.
"""

from __future__ import annotations

from dataclasses import replace

from .protocol import PROTOCOL
from .scalp import ScalpEngine, scorecard

S = PROTOCOL.scalp
ARMS = {
    "B_no_partial": ("one target at 2.5R, no half-off at 1R", replace(S, tp1_r=0.0, tp2_r=2.5)),
    "C_wider_stop": ("stops 0.35-0.90%, target 2.5R", replace(S, stop_min_pct=0.35, stop_max_pct=0.90, tp2_r=2.5)),
    "D_fast_exit": ("time stop 10 min, max hold 45 min", replace(S, time_stop_min=10, max_hold_min=45)),
    "E_all_day": ("trades 24h instead of the 07-11 / 13-17 UTC sessions", replace(S, sessions_utc=(0, 24))),
}


def build(cfg, mkt, P=PROTOCOL) -> dict[str, ScalpEngine]:
    return {name: ScalpEngine(cfg, mkt, P, s=s, name=name) for name, (_, s) in ARMS.items()}


def leaderboard(live: ScalpEngine | None, arms: dict[str, ScalpEngine]) -> str:
    rows = []
    for name, eng in ([("A_live", live)] if live else []) + list(arms.items()):
        rs = [t["result_r"] for t in eng.store.closed(eng._since()) if t.get("result_r") is not None]
        rows.append((name, scorecard(rs)))
    rows.sort(key=lambda r: r[1].get("lo", -99), reverse=True)
    out = ["Scalp arms - paper, same candles, own ledgers. Ranked by the LOW end of the 90% expectancy interval.",
           "arm: trades | win% | R/trade [90% interval] | PF | total R"]
    for name, c in rows:
        if not c["n"]:
            out.append(f"- {name}: no closed trades yet")
            continue
        pf = "inf" if c["pf"] == float("inf") else f"{c['pf']:.2f}"
        out.append(f"- {name}: {c['n']} | {c['win'] * 100:.0f}% | {c['exp']:+.2f} [{c['lo']:+.2f}, {c['hi']:+.2f}] | {pf} | {c['total']:+.1f}")
    out.append("")
    out += [f"{n}: {d}" for n, (d, _) in ARMS.items()]
    out.append("An arm earns real money only when its interval's low end is above 0 over 30+ trades - and only with your go.")
    return "\n".join(out)
