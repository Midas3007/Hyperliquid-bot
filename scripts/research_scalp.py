"""Scalp setup research: test setup families on 1m BTC history with honest costs and walk-forward selection.

    python scripts/research_scalp.py --days 90

Data: Binance BTCUSDT perpetual 1m archive (data.binance.vision, cached in ./data/candles) - Hyperliquid keeps only
5000 1m candles. Costs (Hyperliquid base tier): market entry/stop/time exits pay taker 0.045% + 0.02% slippage; limit
targets pay maker 0.015%. A candle touching stop and target counts as the stop. R = net P&L / net loss at the stop.

Method: every variant is scored on the first 2/3 of the days (in-sample); the best per family (min 20 trades) is then
reported on the last 1/3 it never saw (out-of-sample), with a bootstrap 90% interval. Only an out-of-sample interval
above zero counts.
"""

from __future__ import annotations

import argparse
import itertools
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hlbot.backtest_scalp import binance_day  # noqa: E402

TAKER, SLIP, MAKER = 0.045, 0.02, 0.015
CE = TAKER + SLIP          # market entry / stop / time exit, % of notional
DAYMS = 86_400_000


def load(days: int, cache: str) -> list[dict]:
    end = int(time.time() // 86400 * 86400) - 86400          # the archive lags ~1 day
    out = []
    for k in range(days, 0, -1):
        out += binance_day(time.strftime("%Y-%m-%d", time.gmtime(end - (k - 1) * 86400)), cache)
    return out


def walk(c, i, side, entry, stop, tp, end_i):
    """Follow 1m bars after the entry bar i. -> (R, exit index)."""
    s = 1 if side == "long" else -1
    risk = abs(entry - stop) / entry * 100
    loss = risk + CE + CE
    for j in range(i + 1, min(end_i, len(c))):
        b = c[j]
        if (b["l"] <= stop) if s > 0 else (b["h"] >= stop):
            return -1.0, j
        if tp and ((b["h"] >= tp) if s > 0 else (b["l"] <= tp)):
            return (abs(tp - entry) / entry * 100 - CE - MAKER) / loss, j
    j = min(end_i, len(c)) - 1
    return (s * (c[j]["c"] - entry) / entry * 100 - CE - CE) / loss, j


def net_tp(entry, stop, side, k):
    """Target paying k x the full loss at the stop after all costs."""
    loss = abs(entry - stop) / entry * 100 + CE + CE
    s = 1 if side == "long" else -1
    return entry * (1 + s * (k * loss + CE + MAKER) / 100)


def ny_open_min(day_ms: int) -> int:
    """US cash open in UTC minutes: 13:30 during US daylight saving (2026: 8 Mar - 1 Nov), else 14:30."""
    t = time.gmtime(day_ms / 1000)
    dst = (t.tm_mon, t.tm_mday) >= (3, 8) and (t.tm_mon, t.tm_mday) < (11, 1)
    return 13 * 60 + 30 if dst else 14 * 60 + 30


# ------------------------------------------------------------------ setup 1: opening-range breakout
def orb(c, days_idx, session, or_len, k, stop_mode, min_risk=0.12, max_risk=1.5):
    trades = []
    for d0, idx in days_idx:
        starts = {"london": [7 * 60], "ny": [ny_open_min(d0)], "both": [7 * 60, ny_open_min(d0)]}[session]
        for st in starts:
            a, b = idx.get(st), idx.get(st + or_len)
            end = idx.get(st + 240)
            if a is None or b is None or end is None:
                continue
            hi, lo = max(x["h"] for x in c[a:b]), min(x["l"] for x in c[a:b])
            for i in range(b, end):
                x = c[i]
                side = "long" if x["c"] > hi else "short" if x["c"] < lo else None
                if not side:
                    continue
                entry = x["c"]
                stop = (lo if side == "long" else hi) if stop_mode == "opp" else (hi + lo) / 2
                risk = abs(entry - stop) / entry * 100
                if not (min_risk <= risk <= max_risk):
                    break
                tp = net_tp(entry, stop, side, k) if k else None
                r, _ = walk(c, i, side, entry, stop, tp, end)
                trades.append((x["t"], r))
                break
    return trades


# ------------------------------------------------------------------ setup 2: VWAP band fade in a range
def vwap_fade(c, band, flat_pct, target, hours=(7, 20)):
    trades, busy_until, pv, v, pv2, day = [], 0, 0.0, 0.0, 0.0, -1
    for i, x in enumerate(c):
        if x["t"] // DAYMS != day:
            day, pv, v, pv2 = x["t"] // DAYMS, 0.0, 0.0, 0.0
        tp_ = (x["h"] + x["l"] + x["c"]) / 3
        pv, v, pv2 = pv + tp_ * x["v"], v + x["v"], pv2 + tp_ * tp_ * x["v"]
        if v <= 0 or i < 240 or i < busy_until:
            continue
        hr = (x["t"] % DAYMS) / 3_600_000
        if not (hours[0] <= hr < hours[1]):
            continue
        vw = pv / v
        sd = max(pv2 / v - vw * vw, 0) ** 0.5
        if sd <= 0 or abs(x["c"] / c[i - 240]["c"] - 1) * 100 > flat_pct:
            continue
        side = None
        if x["l"] < vw - band * sd and x["c"] > vw - band * sd:
            side, stop = "long", x["l"] * (1 - 0.0003)
        elif x["h"] > vw + band * sd and x["c"] < vw + band * sd:
            side, stop = "short", x["h"] * (1 + 0.0003)
        if not side:
            continue
        entry = x["c"]
        risk = abs(entry - stop) / entry * 100
        if not (0.08 <= risk <= 1.0):
            continue
        tp = vw if target == "vwap" else net_tp(entry, stop, side, 1.0)
        loss = risk + 2 * CE
        if (abs(tp - entry) / entry * 100 - CE - MAKER) / loss < 1.0:      # never below 1:1 net
            continue
        r, j = walk(c, i, side, entry, stop, tp, i + 120)
        trades.append((x["t"], r))
        busy_until = j + 1
    return trades


# ------------------------------------------------------------------ setup 3: 15m Donchian breakout with trend
def donchian(c, n15, k, hours=(6, 21)):
    """Close of a completed 15m bar beyond the last n15 bars' range, in the direction of the 4h trend.
    Stop at the 15m range midpoint; time exit after 4h."""
    m15 = []
    for i in range(0, len(c) - 15, 15):
        blk = c[i:i + 15]
        m15.append({"i": i + 14, "h": max(x["h"] for x in blk), "l": min(x["l"] for x in blk), "c": blk[-1]["c"], "t": blk[0]["t"]})
    trades, busy = [], 0
    for j in range(max(n15, 16), len(m15)):
        b = m15[j]
        if b["i"] < busy or not (hours[0] <= (b["t"] % DAYMS) / 3_600_000 < hours[1]):
            continue
        hi, lo = max(x["h"] for x in m15[j - n15:j]), min(x["l"] for x in m15[j - n15:j])
        trend = b["c"] - m15[j - 16]["c"]
        side = "long" if b["c"] > hi and trend > 0 else "short" if b["c"] < lo and trend < 0 else None
        if not side:
            continue
        entry, stop = b["c"], (hi + lo) / 2
        risk = abs(entry - stop) / entry * 100
        if not (0.2 <= risk <= 2.0):
            continue
        r, e = walk(c, b["i"], side, entry, stop, net_tp(entry, stop, side, k), b["i"] + 240)
        trades.append((b["t"], r))
        busy = e + 1
    return trades


def score(trades, n_boot=2000):
    rs = [r for _, r in trades]
    n = len(rs)
    if not n:
        return {"n": 0, "exp": -9, "lo": -9, "hi": -9, "win": 0}
    rnd, means = random.Random(1), []
    for _ in range(n_boot):
        means.append(sum(rnd.choice(rs) for _ in range(n)) / n)
    means.sort()
    return {"n": n, "exp": sum(rs) / n, "lo": means[int(.05 * n_boot)], "hi": means[int(.95 * n_boot) - 1],
            "win": sum(r > 0 for r in rs) / n}


def fmt(name, s):
    if not s["n"]:
        return f"{name}: no trades"
    return f"{name}: {s['n']} trades | win {s['win'] * 100:.0f}% | {s['exp']:+.2f}R [{s['lo']:+.2f}, {s['hi']:+.2f}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--cache", default="data/candles")
    a = ap.parse_args()
    c = load(a.days, a.cache)
    split = c[0]["t"] + (c[-1]["t"] - c[0]["t"]) * 2 // 3
    days_idx = {}
    for i, x in enumerate(c):
        d0 = x["t"] // DAYMS * DAYMS
        days_idx.setdefault(d0, {})[int((x["t"] - d0) // 60000)] = i
    days_idx = sorted(days_idx.items())
    print(f"BTCUSDT 1m {time.strftime('%d %b', time.gmtime(c[0]['t'] / 1000))} - "
          f"{time.strftime('%d %b %Y', time.gmtime(c[-1]['t'] / 1000))}, {len(c)} candles; in-sample to "
          f"{time.strftime('%d %b', time.gmtime(split / 1000))}")
    fams = {
        "ORB": [(f"ORB {s} {o}m k{k} {m}", lambda s=s, o=o, k=k, m=m: orb(c, days_idx, s, o, k, m))
                for s, o, k, m in itertools.product(["london", "ny", "both"], [15, 30], [1.0, 1.5, 2.0, 3.0, 0], ["opp", "mid"])],
        "VWAP fade": [(f"VWAP fade {b}sd flat<{f}% to {t}", lambda b=b, f=f, t=t: vwap_fade(c, b, f, t))
                      for b, f, t in itertools.product([2.0, 2.5, 3.0], [0.8, 1.2, 2.0], ["vwap", "1R"])],
        "Donchian 15m": [(f"Donchian15 n{n} k{k}", lambda n=n, k=k: donchian(c, n, k))
                         for n, k in itertools.product([8, 16, 32], [1.0, 1.5, 2.0, 3.0])],
    }
    for fam, variants in fams.items():
        best = None
        for name, fn in variants:
            tr = fn()
            ins = score([t for t in tr if t[0] < split])
            oos = score([t for t in tr if t[0] >= split])
            if ins["n"] >= 20 and (best is None or ins["exp"] > best[1]["exp"]):
                best = (name, ins, oos, score(tr))
        print(f"\n== {fam}: {len(variants)} variants; best in-sample, then judged out-of-sample ==")
        if not best:
            print("no variant with 20+ in-sample trades")
            continue
        print("  " + fmt("in-sample  " + best[0], best[1]))
        print("  " + fmt("OUT-OF-SAMPLE", best[2]) + ("   <- PASSES" if best[2]["lo"] > 0 else ""))
        print("  " + fmt("full period", best[3]))


if __name__ == "__main__":
    main()
