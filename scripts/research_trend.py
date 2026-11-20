"""Trend-following research on Hyperliquid 4h candles (~830 days): does a slow, rule-based trend system have an edge
after costs where 1m scalping did not?

    python scripts/research_trend.py

Systems (long and short, one position per coin):
  * Donchian: enter on a 4h close beyond the last N bars' high/low; initial stop 2x ATR; exit on a close beyond the
    opposite N/2-bar channel (trailing) or the stop.
  * EMA: enter when the fast EMA crosses the slow one; same 2x ATR initial stop; exit on the opposite cross.
Costs: taker 0.045% + 0.02% slippage on entry and exit, funding 0.01%/8h against the position. R = net P&L / initial
risk (entry to stop + costs). Walk-forward: parameters picked on the first 2/3 of the data, judged on the last 1/3.
"""

from __future__ import annotations

import itertools
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hyperliquid.info import Info  # noqa: E402

CE, FUND_8H = 0.065, 0.01
H4 = 4 * 3_600_000


def candles(info, coin):
    now = int(time.time() * 1000)
    raw = info.candles_snapshot(coin, "4h", now - 5000 * H4, now)
    return [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"])} for c in raw][:-1]


def atr(c, i, n=14):
    trs = [max(c[k]["h"] - c[k]["l"], abs(c[k]["h"] - c[k - 1]["c"]), abs(c[k]["l"] - c[k - 1]["c"])) for k in range(i - n + 1, i + 1)]
    return sum(trs) / n


def ema_series(vals, n):
    k, out = 2 / (n + 1), [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def run(c, system, a, b, inv=False):
    trades, pos = [], None
    closes = [x["c"] for x in c]
    ef, es = (ema_series(closes, a), ema_series(closes, b)) if system == "ema" else (None, None)
    start = max(60, b + 2 if system == "ema" else a + 2)
    for i in range(start, len(c)):
        x = c[i]
        if pos:
            s = pos["o"]          # exits follow the original signal's direction
            if system == "donchian":
                m = b
                ch = min(y["l"] for y in c[i - m:i]) if s > 0 else max(y["h"] for y in c[i - m:i])
                exit_sig = (x["c"] < ch) if s > 0 else (x["c"] > ch)
            else:
                exit_sig = (ef[i] < es[i]) if s > 0 else (ef[i] > es[i])
            hit = (x["l"] <= pos["stop"]) if pos["s"] > 0 else (x["h"] >= pos["stop"])
            if hit or exit_sig:
                px = pos["stop"] if hit else x["c"]
                held_h = (x["t"] - pos["t"]) / 3_600_000
                move = pos["s"] * (px - pos["e"]) / pos["e"] * 100
                net = move - 2 * CE - FUND_8H * held_h / 8
                trades.append((pos["t"], net / pos["risk"]))
                pos = None
            continue
        if system == "donchian":
            hi, lo = max(y["h"] for y in c[i - a:i]), min(y["l"] for y in c[i - a:i])
            s = 1 if x["c"] > hi else -1 if x["c"] < lo else 0
        else:
            s = 1 if ef[i] > es[i] and ef[i - 1] <= es[i - 1] else -1 if ef[i] < es[i] and ef[i - 1] >= es[i - 1] else 0
        if s:
            o, s = s, (-s if inv else s)
            stop = x["c"] - s * 2 * atr(c, i)
            risk = abs(x["c"] - stop) / x["c"] * 100 + 2 * CE
            pos = {"s": s, "o": o, "e": x["c"], "stop": stop, "risk": risk, "t": x["t"]}
    return trades


def score(tr, n_boot=2000):
    rs = [r for _, r in tr]
    n = len(rs)
    if n < 2:
        return {"n": n, "exp": -9, "lo": -9, "hi": -9, "win": 0, "tot": sum(rs)}
    rnd, ms = random.Random(1), []
    for _ in range(n_boot):
        ms.append(sum(rnd.choice(rs) for _ in range(n)) / n)
    ms.sort()
    return {"n": n, "exp": sum(rs) / n, "lo": ms[int(.05 * n_boot)], "hi": ms[int(.95 * n_boot) - 1],
            "win": sum(r > 0 for r in rs) / n, "tot": sum(rs)}


def fmt(s):
    return f"{s['n']} trades | win {s['win'] * 100:.0f}% | {s['exp']:+.2f}R [{s['lo']:+.2f}, {s['hi']:+.2f}] | total {s['tot']:+.1f}R"


def main():
    info = Info("https://api.hyperliquid.xyz", skip_ws=True)
    data = {c: candles(info, c) for c in ("BTC", "ETH", "SOL")}
    t0 = min(v[0]["t"] for v in data.values())
    t1 = max(v[-1]["t"] for v in data.values())
    split = t0 + (t1 - t0) * 2 // 3
    day = lambda ms: time.strftime("%d %b %Y", time.gmtime(ms / 1000))  # noqa: E731
    print(f"Hyperliquid 4h, BTC/ETH/SOL, {day(t0)} - {day(t1)}; parameters chosen before {day(split)}")
    grids = {"donchian": list(itertools.product([20, 30, 55], [10, 15, 20])),
             "ema": list(itertools.product([10, 20, 30], [50, 100, 200]))}
    for system, grid in grids.items():
        best = None
        for a, b in grid:
            tr = [t for coin in data for t in run(data[coin], system, a, b)]
            ins = score([t for t in tr if t[0] < split])
            if ins["n"] >= 20 and (best is None or ins["exp"] > best[1]["exp"]):
                best = ((a, b), ins, score([t for t in tr if t[0] >= split]), score(tr), tr)
        (a, b), ins, oos, full, tr = best
        print(f"\n== {system} {a}/{b} (best of {len(grid)} on the first 2/3) ==")
        print("  in-sample     " + fmt(ins))
        print("  OUT-OF-SAMPLE " + fmt(oos) + ("   <- PASSES" if oos["lo"] > 0 else ""))
        print("  full period   " + fmt(full))
        for coin in data:
            print(f"    {coin}: " + fmt(score(run(data[coin], system, a, b))))


if __name__ == "__main__":
    main()
