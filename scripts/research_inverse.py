"""Do the opposite: every tested strategy with the side flipped, stop and target mirrored around the entry, same
costs, same walk-forward judging (variants picked on the first 2/3, judged on the last 1/3).

    python scripts/research_inverse.py --days 32

1. The live scalper and its 7 rule variants (real engine replay). A flipped entry is taken at the signal candle's
   close (the original limit price would be on the wrong side of the market), still charged the maker fee - this
   flatters the flipped version slightly.
2. ORB / VWAP fade / 15m Donchian families (scripts/research_scalp.py with INVERT on).
3. 4h Donchian and EMA trend systems (scripts/research_trend.py, inv=True); exits still follow the original signal.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_scalp as RS  # noqa: E402
import research_trend as RT  # noqa: E402

from hlbot import scalp as SC  # noqa: E402
from hlbot import backtest_scalp as BS  # noqa: E402
from hlbot.arms import ARMS  # noqa: E402
from hlbot.config import CFG  # noqa: E402
from hlbot.protocol import PROTOCOL  # noqa: E402

_orig = SC.find_signal


def flipped(c1, s):
    sig, why = _orig(c1, s)
    if not sig:
        return sig, why
    e = c1[-1]["c"]
    d = abs(sig.entry - sig.stop)
    side = "short" if sig.side == "long" else "long"
    return SC.Signal(sig.setup + " (flipped)", side, e, e + d if side == "short" else e - d, "flipped: " + sig.why), why


def scalper(days):
    candles = RS.load(days, "data/candles")
    warm = 1400
    split = candles[warm]["t"] + (candles[-1]["t"] - candles[warm]["t"]) * 2 // 3
    cfg = replace(CFG, data_dir=tempfile.mkdtemp())
    print(f"\n== 1. Live scalper + variants, flipped, BTC 1m, {days} days ==")
    rows = []
    for name, s in [("A_live", PROTOCOL.scalp)] + [(n, v[1]) for n, v in ARMS.items() if n != "I_claude"]:
        for inv in (False, True):
            print(f"  replaying {name} {'flipped' if inv else 'original'}...", flush=True)
            SC.find_signal = flipped if inv else _orig
            tr = BS.replay(candles, [(0.0, 40)], 5, cfg, PROTOCOL, s=s, warm=warm)
            rs = [(t["created_at"] * 1000, t["result_r"]) for t in tr]
            rows.append((name, inv, RS.score([r for r in rs if r[0] < split]), RS.score([r for r in rs if r[0] >= split]), RS.score(rs)))
    SC.find_signal = _orig
    for name, inv, ins, oos, full in rows:
        print("  " + RS.fmt(f"{name:13s} {'FLIPPED ' if inv else 'original'} full", full))
    best = max((r for r in rows if r[1] and r[2]["n"] >= 20), key=lambda r: r[2]["exp"], default=None)
    if best:
        print("  best flipped variant in-sample: " + best[0])
        print("  " + RS.fmt("in-sample    ", best[2]))
        print("  " + RS.fmt("OUT-OF-SAMPLE", best[3]) + ("   <- PASSES" if best[3]["lo"] > 0 else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=32)
    ap.add_argument("--setup-days", type=int, default=90)
    ap.add_argument("--part", default="123")
    a = ap.parse_args()
    if "1" in a.part:
        scalper(a.days)
    if "2" in a.part:
        setups(a)
    if "3" in a.part:
        trend()


def setups(a):
    print(f"\n== 2. Scalp setup families, flipped, {a.setup_days} days ==")
    RS.INVERT = True
    sys.argv = ["x", "--days", str(a.setup_days)]
    RS.main()


def trend():
    print("\n== 3. 4h trend systems, flipped ==")
    orig_run = RT.run
    RT.run = lambda c, system, x, y: orig_run(c, system, x, y, inv=True)
    RT.main()


if __name__ == "__main__":
    main()
