import math
import random

from hlbot import indicators as ind
from hlbot.levels import Level, build_levels, find_confluence, plan_setup


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


def test_confluence_requires_two_methods():
    lv = [Level(99.0, "BB", "5m", "lower"), Level(99.05, "VWAP", "5m", "-1sd"), Level(98.0, "FIB", "5m", "S1")]
    conf = find_confluence(lv, 100.0, "long", 2.0, 0.25)
    assert conf and set(conf.methods) == {"BB", "VWAP"}
    assert find_confluence([Level(99.0, "BB", "5m", "lower")], 100.0, "long", 2.0, 0.25) is None


def test_plan_setup_respects_rules():
    levels = {
        "5m": [Level(99.0, "BB", "5m", "lower"), Level(99.1, "VWAP", "5m", "vwap"), Level(99.05, "VP", "5m", "VAL")],
        "4h": [Level(98.6, "SWING", "4h", "low"), Level(96.5, "FIB", "4h", "S1"), Level(110.0, "BB", "4h", "upper")],
        "1d": [Level(120.0, "FIB", "1d", "R1")],
    }
    s, why = plan_setup("X", "long", 100.0, levels, min_sl_pct=1.5, max_sl_pct=8, min_rr=3, max_entry_dist_pct=2, tol_pct=0.25)
    assert s, why
    assert s.sl_pct >= 1.5 - 1e-9  # 98.6 swing is too close -> widened to the floor
    assert s.rr >= 3 - 1e-9
    assert s.tp > s.entry > s.sl


def test_build_levels_runs_on_synthetic_data():
    c5 = synth(300, 300_000)
    c4 = synth(180, 14_400_000)
    c1 = synth(90, 86_400_000)
    lv = build_levels(c5, c4, c1)
    for tf in ("5m", "4h", "1d"):
        assert {l.method for l in lv[tf]} >= {"BB", "FIB", "VWAP", "VP"}
