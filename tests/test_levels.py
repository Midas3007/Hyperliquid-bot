import math
import random

from hlbot import indicators as ind
from hlbot.levels import build_levels


def synth(n, step_ms, start=100.0, vol=0.004, seed=1):
    rnd = random.Random(seed)
    out, px, t = [], start, 1_700_000_000_000
    for _ in range(n):
        o = px
        c = o * (1 + rnd.gauss(0, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": rnd.uniform(100, 1000)})
        px, t = c, t + step_ms
    return out


def test_indicators_basic():
    c = synth(100, 300_000)
    bb = ind.bollinger(c)
    assert bb["lower"] < bb["mid"] < bb["upper"]
    vp = ind.volume_profile(c)
    assert vp["val"] <= vp["poc"] <= vp["vah"]
    p = ind.fib_pivots(110, 90, 100)
    assert p["S1"] < p["P"] < p["R1"]
    assert math.isclose(p["R1"] - p["P"], 0.382 * 20)
    vr = ind.realized_vol_rank(synth(24 * 30, 3_600_000, vol=0.01))
    assert 0 <= vr["rank"] <= 100 and vr["label"] in ("LOW", "NORMAL", "HIGH", "EXTREME")


def test_build_levels_runs_on_synthetic_data():
    c5 = synth(300, 300_000)
    c4 = synth(180, 14_400_000)
    c1 = synth(90, 86_400_000)
    lv = build_levels(c5, c4, c1)
    for tf in ("5m", "4h", "1d"):
        assert {l.method for l in lv[tf]} >= {"BB", "FIB", "VWAP", "VP"}
