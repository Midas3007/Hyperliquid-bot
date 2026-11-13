"""Rules engine: gate, screener, filters, TP/SL/entry, direction, breakers, schedule, review, final check, config."""

import dataclasses
from datetime import datetime, timezone

import pytest

from hlbot.levels import Level
from hlbot.protocol import PROTOCOL as P
from hlbot.protocol import load, load_calendar
from hlbot.rules import bracket, breakers, direction, filters, gate, metrics, review, schedule, screen

dirn, sch = direction, schedule
from hlbot.rules.card import Plan, final_check, render
from hlbot.rules.checks import Datum, usable
from hlbot.rules.sizing import Fees, SizingInput, size


def ts(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


TUE = ts(2026, 11, 10, 12)  # a Tuesday


# ---------------------------------------------------------------- config + data conventions
def test_config_is_strict(tmp_path):
    text = open("protocol.toml").read()
    bad = tmp_path / "p.toml"
    bad.write_text(text.replace("normal_pct = 10.0", "normal_pct = 10.0\nnormal_pc = 9"))
    with pytest.raises(KeyError, match="normal_pc"):
        load(str(bad))
    bad.write_text(text.replace("half_pct = 5.0\n", ""))
    with pytest.raises(KeyError, match="half_pct"):
        load(str(bad))
    bad.write_text(text.replace("normal_pct = 10.0", 'normal_pct = "10"'))
    with pytest.raises(TypeError):
        load(str(bad))
    assert P.risk.ceiling_pct == 15.0 and P.exchange.min_order_usd == 10.0


def test_calendar_loads():
    c = load_calendar()
    assert c.unlocks_covered_until is None  # no unlock data shipped: unlock check fails until filled in
    assert any(e.name == "FOMC" for e in c.macro)


def test_missing_stale_zero_fail():
    now = 1_000_000.0
    assert usable(Datum(1.2, "x", now - 60), now, 15) == (1.2, None)
    assert usable(Datum(1.2, "x", now - 16 * 60), now, 15)[1].startswith("stale")
    assert usable(Datum(0.0, "x", now), now, 15)[1].startswith("zero")
    assert usable(None, now, 15)[1] == "missing"
    assert usable(Datum(0.0, "x", now), now, 15, zero_is_missing=False) == (0.0, None)


# ---------------------------------------------------------------- Module 1
def test_btc_bands():
    g = P.gate
    assert gate.btc_band(-4.0, g) == "no_trade" and gate.btc_band(-3.99, g) == "half"
    assert gate.btc_band(-2.0, g) == "half" and gate.btc_band(-1.99, g) == "normal"
    assert gate.btc_band(2.99, g) == "normal" and gate.btc_band(3.0, g) == "pump"
    assert gate.btc_band(None, g) is None
    assert gate.is_stricter("half", "normal") and not gate.is_stricter("pump", "normal")


def test_post_pump_flags():
    flags, na = gate.post_pump_flags(P.gate, funding_8h_pct=0.04, funding_rising=True, oi_change_24h_pct=5,
                                     last_4h_move_pct=0.1, rejection_at_resistance=False,
                                     whale_ls_now=2.4, whale_ls_24h_ago=2.0)
    assert len(flags) == 3 and gate.reversal_risk(flags, P.gate) and not na
    flags, na = gate.post_pump_flags(P.gate, funding_8h_pct=None, funding_rising=None, oi_change_24h_pct=None,
                                     last_4h_move_pct=None, rejection_at_resistance=None,
                                     whale_ls_now=None, whale_ls_24h_ago=None)
    assert flags == [] and len(na) == 4


# ---------------------------------------------------------------- Module 2
def test_sweeps_walk_past_skips():
    rows = [screen.Row("A", 60e6, 5.0, 1.0), screen.Row("B", 60e6, 4.0, 9.0), screen.Row("C", 200e6, 3.0, 6.0),
            screen.Row("D", 40e6, 9.0, 1.0), screen.Row("E", 300e6, None, 12.0), screen.Row("F", 300e6, 2.0, -3.0)]
    s = dataclasses.replace(P.screener, sweep1_top_n=2, sweep2_top_n=2)
    out = screen.sweeps(rows, s, skip=lambda c: "skip" if c == "A" else None)
    # sweep 1 (vol > $50M, by buy/sell): A skipped, D too small -> B, C. Sweep 2 (vol > $100M, by day change): E.
    assert out == [("B", "sweep 1: buy/sell"), ("C", "sweep 1: buy/sell"), ("E", "sweep 2: momentum")]
    # sweep 3 (v3.1): heaviest sell flow, so trend shorts can be found
    rows += [screen.Row("G", 60e6, 0.3, -2.0), screen.Row("H", 60e6, 0.6, -1.0), screen.Row("I", 30e6, 0.1, -5.0)]
    out = screen.sweeps(rows, s, skip=lambda c: None)
    assert [c for c, tag in out if tag == "sweep 3: sell flow"] == ["G", "H"]


def test_cooldowns_same_for_every_coin():
    s = P.screener
    now = TUE
    assert screen.cooldown_until("SUI", [("SUI", now - 3600)], now, s)[0] == pytest.approx(now - 3600 + 86400)
    assert screen.cooldown_until("SUI", [("SUI", now - 30 * 3600)], now, s) is None
    two = [("TAO", now - 20 * 86400), ("TAO", now - 2 * 86400)]
    until, why = screen.cooldown_until("TAO", two, now, s)
    assert until == pytest.approx(now - 2 * 86400 + 14 * 86400) and "14-day" in why
    assert screen.cooldown_until("TAO", [("TAO", now - 40 * 86400), ("TAO", now - 2 * 86400)], now, s) is None


def test_auto_skips():
    s = P.screener
    kw = dict(unlock=None, vol_label="NORMAL", cooldown=None)
    assert screen.auto_skips(s, coin="SOL", **kw) == []  # v3.1: unlock check off, so missing unlock data is fine
    assert screen.auto_skips(s, coin="USDE", **kw)[0].rule == "stablecoin"
    assert screen.auto_skips(s, coin="kPEPE", **kw) == []
    x = screen.auto_skips(s, coin="SOL", **{**kw, "vol_label": "EXTREME"})[0]
    assert x.rule == "EXTREME volatility" and x.hard
    assert screen.auto_skips(s, coin="SOL", **{**kw, "cooldown": (TUE, "SL 2h ago")})[0].rule == "cooldown"
    on = dataclasses.replace(s, unlock_check=True)
    assert screen.auto_skips(on, coin="SOL", **kw)[0].hard  # with the check on, no unlock data = FAIL
    assert screen.auto_skips(on, coin="SOL", **{**kw, "unlock": (True, "unlock 2%")})[0].rule == "token unlock"
    ev = [("SUI", TUE + 30 * 3600, 2.0), ("SUI", TUE + 10 * 86400, 5.0), ("ETH", TUE + 3600, 9.0)]
    assert screen.unlock_in_window(ev, TUE + 5 * 86400, "SUI", TUE, TUE + 10 * 3600, s)[0] is True  # hold + 24h
    assert screen.unlock_in_window(ev, TUE + 5 * 86400, "SOL", TUE, TUE + 10 * 3600, s)[0] is False
    assert screen.unlock_in_window(ev, TUE + 3600, "SOL", TUE, TUE + 10 * 3600, s) is None  # calendar too short


# ---------------------------------------------------------------- Module 3
NEWS_OK = filters.News(2, 0, 3, None)


def hf(side="long", **kw):
    base = dict(day_change_pct=1.0, whale_ls=1.8, whale_is_true=True, crowd_ls=None, oi_change_24h_pct=0.5,
                vol_label="NORMAL", news=NEWS_OK)
    base.update(kw)
    return {c.rule: c for c in filters.hard_filters(side, P.filters, P.screener, **base)}


def test_hard_filters():
    assert all(c.ok for c in hf().values())
    assert not hf(whale_ls=1.4)["whale L/S"].ok
    assert not hf(oi_change_24h_pct=-1.5)["OI 24h"].ok and hf(oi_change_24h_pct=-0.9)["OI 24h"].ok
    assert "caution" in hf(vol_label="HIGH")["volatility"].detail and hf(vol_label="HIGH")["volatility"].ok
    assert hf(vol_label="EXTREME")["volatility"].hard
    assert not hf(news=filters.News(1, 1, 3, None))["news"].ok
    assert not hf(news=filters.News(3, 0, 3, "exchange exploit"))["news"].ok
    assert hf(news=None)["news"].hard and hf(oi_change_24h_pct=None)["OI 24h"].hard
    assert hf(whale_is_true=False, whale_ls=None, crowd_ls=2.0)["crowd L/S"].ok
    assert not hf(whale_is_true=False, whale_ls=None, crowd_ls=3.0)["crowd L/S"].ok
    assert "red day" in hf(day_change_pct=-3)["24h price"].detail
    # shorts mirror: whales must lean short, news bearish, not up more than 8%
    s = hf("short", whale_ls=0.6, news=filters.News(0, 2, 3, None), day_change_pct=9)
    assert s["whale L/S"].ok and s["news"].ok and not s["24h price"].ok


def test_ranking():
    n, pts = filters.rank_score("long", P.ranking, whale_ls=2.1, oi_growth_30d_pct=4, news=filters.News(3, 0, 3, None),
                                vol_label="LOW", net_rr=4.2, tp_adr_mult=0.9)
    assert n == 6
    n, _ = filters.rank_score("long", P.ranking, whale_ls=1.9, oi_growth_30d_pct=None, news=NEWS_OK,
                              vol_label="NORMAL", net_rr=3.5, tp_adr_mult=1.1)
    assert n == 0


# ---------------------------------------------------------------- Modules 4, 6, 7 placement
def lv(price, method, name, tf="4h"):
    return Level(price, method, tf, name)


def test_entry_confluence_needs_three_of_four_methods():
    e = P.entry
    assert e.min_methods == 3
    five = [lv(99.60, "BB", "lower", "5m"), lv(99.62, "VWAP", "-1sd", "5m"), lv(99.58, "FIB", "S1", "5m"),
            lv(99.64, "VP", "VAL", "5m"), lv(98.0, "BB", "mid", "5m")]
    c, _ = bracket.entry_confluence(five, 100.0, "long", e)
    assert c and c.methods == ("BB", "FIB", "VP", "VWAP") and c.price == pytest.approx(99.61)  # 4 beats 3
    c, _ = bracket.entry_confluence(five[:3], 100.0, "long", e)
    assert c and c.methods == ("BB", "FIB", "VWAP")
    c, why = bracket.entry_confluence(five[:2], 100.0, "long", e)
    assert c is None and "3 of" in why
    far = [dataclasses.replace(x, price=x.price - 1.0) for x in five[:4]]  # ~1.4% away > 1% proximity
    assert bracket.entry_confluence(far, 100.0, "long", e)[0] is None
    assert bracket.entry_confluence(five, 100.0, "short", e)[0] is None  # nothing above price


STRUCT = [lv(97.0, "SWING", "low"), lv(97.05, "VP", "VAL"), lv(103.0, "SWING", "high"), lv(104.0, "FIB", "R1"),
          lv(101.0, "BB", "upper")]


def test_structural_levels_merge_and_exclude_dynamic():
    s = bracket.structural(STRUCT, P.tp)
    assert [round(x.price, 3) for x in s] == [97.025, 103.0, 104.0]  # BB not structural; 97/97.05 merged


def test_stop_loss_beyond_structure_with_floor():
    s = bracket.structural(STRUCT, P.tp)
    sl, how = bracket.stop_loss(99.6, "long", s, atr_4h=0.8, r=P.risk)
    assert sl == pytest.approx(97.025 - 0.2) and how.startswith("past 4h")
    near = bracket.structural([lv(99.4, "SWING", "low"), lv(103, "SWING", "high")], P.tp)
    sl, how = bracket.stop_loss(99.6, "long", near, atr_4h=0.2, r=P.risk)
    assert sl == pytest.approx(99.6 * 0.985) and "1.5% floor" in how
    assert bracket.stop_loss(99.6, "long", [], atr_4h=0.2, r=P.risk)[0] is None
    sl, _ = bracket.stop_loss(100.4, "short", s, atr_4h=0.8, r=P.risk)
    assert sl == pytest.approx(103.2)


def test_take_profit_rule():
    s = bracket.structural(STRUCT, P.tp)
    tp, _ = bracket.take_profit(99.6, 100.0, "long", s, atr_4h_pct=1.0, adr_pct=4.0, t=P.tp)
    # first 4H level above price is 103.0 (clean air); front-run by max(0.2%, 0.1 x 1.0%) = 0.2%
    assert tp.tp == pytest.approx(103.0 * 0.998) and tp.level.price == 103.0
    assert tp.dist_pct == pytest.approx((103 * 0.998 / 99.6 - 1) * 100)
    tp, _ = bracket.take_profit(99.6, 100.0, "long", s, atr_4h_pct=3.0, adr_pct=4.0, t=P.tp)
    assert tp.tp == pytest.approx(103.0 * 0.997)  # 0.1 x ATR (0.3%) beats 0.2%
    none, why = bracket.take_profit(99.6, 100.0, "long", s, atr_4h_pct=1.0, adr_pct=2.5, t=P.tp)
    assert none is None and "ADR" in why  # 3.2% > 1.2 x 2.5%
    assert bracket.take_profit(99.6, 100.0, "long", s, atr_4h_pct=1.0, adr_pct=None, t=P.tp)[0] is None
    tp, _ = bracket.take_profit(100.4, 100.0, "short", s, atr_4h_pct=1.0, adr_pct=4.0, t=P.tp)
    assert tp.tp == pytest.approx(97.025 * 1.002)


# ---------------------------------------------------------------- Module 5
def test_direction():
    d, sw, dy = P.direction, P.strategy.swing, P.strategy.day
    lt = dirn.long_triggers(d, sw, buy_sell=3.5, whale_ls=1.8, funding_8h=0.01, btc_band="normal", fng=40)
    assert sum(t.hit for t in lt) == 4
    na = dirn.long_triggers(d, sw, buy_sell=None, whale_ls=1.8, funding_8h=0.01, btc_band="normal", fng=40)
    assert sum(t.hit for t in na) == 3 and na[0].detail == "N/A"  # swing: N/A counts as a miss
    st = dirn.trend_short_triggers(d, sw, buy_sell=0.25, whale_ls=0.6, funding_8h=0.0, btc_broke_4h_support=False, fng=55)
    assert sum(t.hit for t in st) == 2
    rv = dirn.reversal_short_triggers(d, rejection_wick=True, funding_8h=0.04, funding_rising=True, oi_change_24h=-6,
                                      day_change=6, whale_ls_now=0.9, whale_ls_24h_ago=1.3, btc_vol_now="HIGH",
                                      btc_vol_24h_ago="NORMAL", btc_change=3.5)
    assert sum(t.hit for t in rv) == 5
    none = [dirn.Trigger("x", False, "N/A")] * 5
    assert dirn.decide(d, sw, lt, none, none)[0] == ["long"]
    assert dirn.decide(d, sw, lt, none, rv)[0] == ["long", "short"]
    assert dirn.decide(d, sw, na, none, rv)[0] == ["short"]
    assert dirn.decide(d, sw, lt, st, none)[0] == []
    assert dirn.decide(d, sw, na, st, none)[0] == ["short"]
    assert dirn.decide(d, sw, na, none, none)[0] == []
    # day: a trigger with no data is left out; 3 of the 4 measurable long triggers are enough
    dl = dirn.long_triggers(d, dy, buy_sell=2.6, whale_ls=None, funding_8h=0.01, btc_band="normal", fng=62)
    assert dirn.decide(d, dy, dl, none, none)[0] == ["long"]
    weak = dirn.long_triggers(d, dy, buy_sell=2.0, whale_ls=None, funding_8h=0.01, btc_band="normal", fng=40)
    assert dirn.decide(d, dy, weak, none, none)[0] == []  # only funding and BTC hit
    thin = dirn.long_triggers(d, dy, buy_sell=None, whale_ls=None, funding_8h=0.01, btc_band="normal", fng=62)
    assert dirn.decide(d, dy, thin, none, none)[0] == []  # 3 hits, but only 3 measurable (day needs 4)


def test_day_and_swing_holds_and_mode_aware_filters():
    s = P.schedule
    assert sch.time_stop_at(TUE, s, 24) == TUE + 24 * 3600
    assert sch.time_stop_at(TUE, s) == TUE + 48 * 3600
    kw = dict(day_change_pct=1.0, whale_ls=None, crowd_ls=None, oi_change_24h_pct=0.5, vol_label="NORMAL")
    day = {c.rule: c for c in filters.hard_filters("long", P.filters, P.screener, news=filters.News(0, 0, 1, None),
                                                   whale_absent=True, na_is_miss=False, news_optional_below=2, **kw)}
    assert day["whale L/S"].ok and not day["whale L/S"].hard and day["news"].ok
    cat = {c.rule: c for c in filters.hard_filters("long", P.filters, P.screener, news=filters.News(0, 0, 1, "exploit"),
                                                   whale_absent=True, na_is_miss=False, news_optional_below=2, **kw)}
    assert not cat["news"].ok  # a thesis-breaking catalyst still blocks
    swing = {c.rule: c for c in filters.hard_filters("long", P.filters, P.screener, news=filters.News(0, 0, 3, None),
                                                     whale_absent=True, na_is_miss=True, news_optional_below=0, **kw)}
    assert not swing["whale L/S"].ok and not swing["whale L/S"].hard  # a soft miss, not missing data
    assert not swing["news"].ok and not swing["news"].hard
    none = {c.rule: c for c in filters.hard_filters("long", P.filters, P.screener, news=None, **kw)}
    assert none["whale L/S"].hard and none["news"].hard  # no leaderboard data at all = missing = hard


# ---------------------------------------------------------------- Module 8
def ex(t, kind, coin="X"):
    return breakers.Exit(coin, kind, t)


def bk(**kw):
    base = dict(exits=[], entries_today=[], equity=100, peak_equity=100, dd_half_was_active=False, dd_stop=None,
                last_review_at=None, protocol_version="3.0", btc_band="normal", now=TUE)
    base.update(kw)
    return breakers.evaluate(P.breakers, **base)


def test_breakers():
    assert bk().status() == "none active"
    assert bk(exits=[ex(TUE - 3600, "sl")]).blocked  # SL today
    assert not bk(exits=[ex(TUE - 86400, "sl")]).blocked
    two = [ex(TUE - 5 * 86400, "sl"), ex(TUE - 3 * 86400, "time"), ex(TUE - 2 * 86400, "sl")]
    st = bk(exits=two)
    assert st.sl_streak == 2 and st.half_risk and not st.blocked
    assert not bk(exits=two + [ex(TUE - 86400, "tp")]).half_risk  # a TP resets
    three = two + [ex(TUE - 30 * 3600, "sl")]
    assert "paused until" in bk(exits=three).blocked[0]
    after = TUE + 30 * 3600
    assert "review" in bk(exits=three, now=after).blocked[0]
    assert not bk(exits=three, now=after, last_review_at=after - 60).blocked
    # drawdown hysteresis: 30% -> half risk until back within 15%
    assert bk(equity=69, peak_equity=100).dd_half_active
    assert bk(equity=80, peak_equity=100, dd_half_was_active=True).dd_half_active
    assert not bk(equity=86, peak_equity=100, dd_half_was_active=True).dd_half_active
    # 50% stop: 7 days, then review, then a new protocol version
    stop = {"since": TUE - 8 * 86400, "version": "3.0"}
    assert "7-day stop starts now" in bk(equity=49, peak_equity=100).blocked[0]
    assert "review" in bk(dd_stop=stop).blocked[0]
    assert "next protocol version" in bk(dd_stop=stop, last_review_at=TUE - 3600).blocked[0]
    assert not bk(dd_stop=stop, last_review_at=TUE - 3600, protocol_version="3.1").blocked
    # entries per day: 2 normally, 1 in the half band, no re-entries there
    assert bk(entries_today=["A", "B"]).blocked and not bk(entries_today=["A"]).blocked
    assert bk(entries_today=["A"], btc_band="half").blocked
    assert breakers.reentry_blocked("A", ["A"], "half") and not breakers.reentry_blocked("A", ["A"], "normal")


# ---------------------------------------------------------------- Module 9 / 0 / 6
def test_schedule_windows():
    s = P.schedule
    assert schedule.entry_window_open(ts(2026, 11, 12, 23, 59), s)          # Thu 23:59
    assert not schedule.entry_window_open(ts(2026, 11, 13, 0, 0), s)        # Fri 00:00
    assert schedule.must_cancel_entries(ts(2026, 11, 13, 0, 0), s)
    assert not schedule.must_be_flat(ts(2026, 11, 13, 19, 59), s) and schedule.must_be_flat(ts(2026, 11, 13, 20), s)
    assert schedule.must_be_flat(ts(2026, 11, 15, 12), s)                   # Sunday
    assert schedule.time_stop_at(TUE, s) == TUE + 48 * 3600
    thu = ts(2026, 11, 12, 10)
    assert schedule.time_stop_at(thu, s) == ts(2026, 11, 13, 20)            # Friday flatten comes first
    start, end = schedule.planned_hold(TUE, s, P.entry)
    assert end == TUE + 52 * 3600


def test_macro_events():
    s = P.schedule
    ev = [("CPI", TUE + 10 * 3600), ("PPI", TUE + 3600)]
    hits, _ = schedule.macro_in_window(ev, TUE + 10 * 86400, TUE, TUE + 52 * 3600, s)
    assert hits == [("CPI", TUE + 10 * 3600)]
    assert schedule.macro_in_window(ev, TUE + 3600, TUE, TUE + 52 * 3600, s)[0] is None


def test_cancel_triggers():
    kw = dict(side="long", created_at=TUE, now=TUE + 3600, entry=100, sl=98, tp=106, price=101,
              band_at_order="normal", band_now="normal", last_4h_close=100.5, stricter=False)
    assert schedule.cancel_triggers(P.entry, **kw) == []
    assert "4h passed" in schedule.cancel_triggers(P.entry, **{**kw, "now": TUE + 4 * 3600})[0]
    assert "stricter" in schedule.cancel_triggers(P.entry, **{**kw, "band_now": "half", "stricter": True})[0]
    assert "half the way" in schedule.cancel_triggers(P.entry, **{**kw, "price": 103.1})[0]
    assert "4H candle closed beyond the SL" in schedule.cancel_triggers(P.entry, **{**kw, "last_4h_close": 97.9})[0]


def test_early_exits():
    s = P.schedule
    assert schedule.early_exit_suggestions("long", -4.2, None, s)
    assert not schedule.early_exit_suggestions("short", -4.2, None, s)
    assert schedule.early_exit_suggestions("short", 4.0, [("FOMC", TUE)], s)[1].startswith("FOMC")


# ---------------------------------------------------------------- metrics
def candle(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": 1}


def test_metrics():
    day = 86_400_000
    daily = [candle(i * day, 100, 104, 98, 101) for i in range(15)]
    assert metrics.adr_pct(daily[:14], 14) == pytest.approx(6.0)
    assert metrics.adr_pct(daily[:10], 14) is None
    assert metrics.utc_day_open(daily, 14 * day + 5000) == 100
    assert metrics.funding_8h(0.00125) == pytest.approx(0.01)
    assert metrics.funding_rising([0.001] * 8 + [0.002]) and not metrics.funding_rising([0.002] * 9)
    assert metrics.rejection_wick(candle(0, 100, 105, 99.5, 100.2), 104.9, "short", 0.5, 0.2)
    assert not metrics.rejection_wick(candle(0, 100, 105, 99.5, 104.8), 104.9, "short", 0.5, 0.2)
    assert metrics.closed_below([candle(0, 0, 0, 0, 101), candle(1, 0, 0, 0, 99)], [100, 95]) == 100
    flat = [candle(i, 100, 100, 100, 100) for i in range(800)]
    assert metrics.rv_percentile(flat, 24, 30) is None  # zero vol = missing, not low
    assert metrics.vol_label(85, 25, 75, 85) == "EXTREME" and metrics.vol_label(24, 25, 75, 85) == "LOW"


# ---------------------------------------------------------------- Module 11
def lt(r, kind="tp", **kw):
    return review.LoggedTrade(result_usd=r * 2, result_r=r, exit_type=kind, **kw)


def test_review_and_recalibration():
    trades = [lt(3.0), lt(-1.0, "sl"), lt(-1.0, "sl"), lt(3.1), lt(-0.4, "time", mfe_r=0.5),
              lt(1.2, "discretion", bracket_r=3.0), lt(-1.0, "sl", override=True)]
    s = review.summarize(trades, P.review, P.risk)
    assert s["trades"] == 7 and s["hit_rate"] == pytest.approx(3 / 7 * 100)
    assert s["expectancy_r"] == pytest.approx(sum(t.result_r for t in trades) / 7)
    assert s["longest_losing_streak"] == 2 and s["max_drawdown_r"] == pytest.approx(2.0)
    assert s["discretion"]["actual_r"] == 1.2 and s["discretion"]["bracket_r"] == 3.0
    assert s["overrides"] == (1, -1.0) and s["losers_green_first"] == 1
    assert s["recalibrated_risk_pct"] is None  # needs 30 trades
    # p = 0.4, b = 3 -> 0.5 x (0.4 - 0.6/3) = 10%
    thirty = [lt(3.0)] * 12 + [lt(-1.0, "sl")] * 18
    assert review.recalibrate(thirty, P.review, P.risk) == pytest.approx(10.0)
    # p = 0.25, b = 3 -> 0 -> no edge
    assert review.recalibrate([lt(3.0)] * 5 + [lt(-1.0, "sl")] * 15, P.review, P.risk) == 0.0
    text = review.format_review(review.summarize(thirty, P.review, P.risk), "REVIEW", P.review)
    assert "Recalibrated risk from the last 30 trades: 10.0%" in text
    assert not review.discretion_restricted([lt(1, "discretion", bracket_r=0.5)] * 10, P.review)
    assert review.discretion_restricted([lt(0.5, "discretion", bracket_r=1.0)] * 10, P.review)


# ---------------------------------------------------------------- card + Module 13 final check
def plan(**kw):
    z = size(SizingInput("long", 92.5, 91.1125, 97.2175, 25, 25, 10, False, 2, [(0, 50)],
                         Fees(0.015, 0.045, 0.05), 0.01), P.risk, P.exchange, P.filters)
    base = dict(coin="HYPE", side="long", entry=92.5, sl=91.1125, tp=97.2175, entry_methods=("BB", "FIB", "VP", "VWAP"),
                sl_label="past 4h SWING low 91.5", tp_label="4h SWING high 97.4", tp_adr_mult=0.9, sizing=z,
                fees=Fees(0.015, 0.045, 0.05, "userFees"), equity=25, equity_ts=TUE, btc_band="normal",
                vol_label="NORMAL", score=3, score_points=["a", "b", "c"], data_ts=TUE, breakers="none active")
    base.update(kw)
    return Plan(**base)


def test_card_and_final_check():
    p = plan()
    text = render(p, P, TUE, paper=True)
    for s in ("HEVI", "Protocol v3.2", "PAPER", "post-only (ALO)", "Isolated", "Leverage:    22x", "Net R/R:     1:3.06",
              "mark-price trigger", "Risk mode:   Normal 10%", "TP slippage ~$0.08"):
        assert s in text, s
    assert final_check(p, P, now=TUE + 60, open_positions=0, pending_entries=0, breakers_blocked=[],
                       authorized=True) == []
    bad = final_check(p, P, now=TUE + 20 * 60, open_positions=1, pending_entries=0, breakers_blocked=["SL today"],
                      authorized=False)
    assert len(bad) == 5  # no y, open position, breaker, stale card data, stale equity - and nothing else
    fri = ts(2026, 11, 13, 9)
    assert any("Friday" in b for b in final_check(plan(data_ts=fri, equity_ts=fri), P, now=fri, open_positions=0,
                                                  pending_entries=0, breakers_blocked=[], authorized=True))
    assert any("4H level" in b for b in final_check(plan(tp_label="1d BB upper"), P, now=TUE, open_positions=0,
                                                    pending_entries=0, breakers_blocked=[], authorized=True))


def test_go_live_gate():
    a = P.autopilot
    assert a.go_live_min_trades == 10
    ok, why = review.go_live_ready([lt(3.0)] * 5, a)
    assert not ok and "5/10" in why
    assert not review.go_live_ready([lt(3.0)] * 3 + [lt(-1.0, "sl")] * 6, a)[0]  # 9 trades: one short
    assert review.go_live_ready([lt(3.0)] * 4 + [lt(-1.0, "sl")] * 6, a)[0]  # 10 trades, +0.6R/trade, PF 2.0
    ok, _ = review.go_live_ready([lt(3.0)] * 6 + [lt(-1.0, "sl")] * 14, a)  # +0.2R/trade, PF 1.29
    assert ok
    ok, why = review.go_live_ready([lt(3.0)] * 5 + [lt(-1.0, "sl")] * 15, a)  # 0R/trade
    assert not ok and "expectancy" in why
    ok, why = review.go_live_ready([lt(0.3)] * 10 + [lt(-0.25, "sl")] * 10, a)  # +0.025R/trade
    assert not ok


def test_early_exits_reviewed_against_bracket():
    s = review.summarize([lt(-0.4, "early", bracket_r=-1.0), lt(0.5, "early", bracket_r=3.0)], P.review, P.risk)
    e = s["early"]
    assert (e["n"], e["compared"]) == (2, 2) and e["actual_r"] == pytest.approx(0.1) and e["bracket_r"] == 2.0
    assert "Early exits (objective triggers) 2" in review.format_review(s, "R", P.review)
