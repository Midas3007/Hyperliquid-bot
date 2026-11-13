"""Offline end-to-end runs of HEVI's engine on the paper trader with a crafted market.

SOL is built to pass every rule: 4-method 5m confluence 0.4% under price, 4H swing support under it, the next 4H
level +6% above (clean air), ADR 6%, whale L/S 1.8, buy/sell 3.5, funding 0.01%/8h, F&G 62, 2/3 news bullish.
"""

import dataclasses
import time
from datetime import datetime, timezone

import pytest

from hlbot import engine as engine_mod
from hlbot.config import Config
from hlbot.engine import Engine, Proposal
from hlbot.levels import Level
from hlbot.paper import PaperTrader
from hlbot.protocol import PROTOCOL
from hlbot.store import Store

TUE = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc).timestamp()
STEP = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
COINS = {  # coin: (volume, buy/sell, day open, whale L/S)
    "BTC": (2e9, 1.0, 99.0, 1.2), "ETH": (900e6, 1.2, 99.5, 1.3), "SOL": (400e6, 3.5, 99.0, 1.8),
    "DOGE": (300e6, 2.0, 110.0, 1.6), "USDE": (200e6, 5.0, 100.0, 1.5),
}

CALENDAR = """
[macro]
covered_until = "2027-12-31T00:00:00Z"
events = []
[unlocks]
covered_until = "2027-12-31T00:00:00Z"
events = []
"""


def levels(*_):
    lv = lambda p, m, tf, n: Level(p, m, tf, n)  # noqa: E731
    return {
        "5m": [lv(99.60, "BB", "5m", "lower"), lv(99.62, "VWAP", "5m", "-1sd"), lv(99.58, "FIB", "5m", "S1"),
               lv(99.64, "VP", "5m", "VAL"), lv(100.6, "BB", "5m", "upper")],
        "4h": [lv(98.4, "SWING", "4h", "low"), lv(106.0, "SWING", "4h", "high"), lv(90.0, "VP", "4h", "POC"),
               lv(110.0, "FIB", "4h", "R1"), lv(101.0, "BB", "4h", "upper")],
        "1d": [lv(115.0, "BB", "1d", "upper"), lv(85.0, "FIB", "1d", "S1")],
    }


class FakeFlow:
    def ensure(self, coins):
        pass


class FakeMarket:
    def __init__(self, store):
        self.store = store
        self.flow = FakeFlow()
        self.mark = {c: 100.0 for c in COINS}
        self.news_items = {"SOL": 3}
        self.fng = 62
        self.macro: list = []
        self.macro_cov = False
        self.absent = set()   # coins no top-60 leaderboard account holds
        self.adr_rng = 6.0    # 1d candle range in %, i.e. the 14-day ADR

    def stamp(self, key):
        return time.time()

    def ctxs(self):
        return {c: {"coin": c, "mark": self.mark[c], "mid": self.mark[c], "volume_usd": v, "oi_usd": 50e6,
                    "funding_1h_pct": 0.00125, "funding_8h_pct": 0.01, "sz_decimals": 2, "max_leverage": 20,
                    "margin_tiers": [(0.0, 20)], "prev_day": 99.0, "change_pct": 1.0}
                for c, (v, *_rest) in COINS.items()}

    def day_open(self, coin):
        return COINS[coin][2]

    def buy_sell(self, coin):
        return {"ratio": COINS[coin][1], "ts": time.time()}

    def whales(self):
        return {c: {"ratio": w, "holders": 12, "accounts_scanned": 60} for c, (_, _, _, w) in COINS.items() if c not in self.absent}

    def candles(self, coin, interval, n):
        step = STEP[interval]
        last = int(time.time() * 1000) // step * step
        rng = {"5m": 0.2, "1h": 0.5, "4h": 1.0, "1d": self.adr_rng}[interval]
        return [{"t": last - (n - 1 - i) * step, "o": 100.0, "h": 100 + rng / 2, "l": 100 - rng / 2, "c": 100.0, "v": 10}
                for i in range(n)]

    def candles_range(self, coin, interval, s, e):
        return []

    def funding_hist(self, coin, hours=12):
        return [0.00125] * 12

    def dvol(self, coin):
        return None

    def news(self, coin, limit=5):
        return [{"title": f"{coin} story {i}", "summary": "", "age_h": 1, "source": "test"}
                for i in range(min(limit, self.news_items.get(coin, 3)))]

    def fear_greed(self):
        return {"value": self.fng, "label": "Greed"}

    def macro_calendar(self, names):
        return self.macro, (time.time() + 7 * 86400 if self.macro_cov else None)

    def record_oi(self):
        pass


class FakeAnalyst:
    def __init__(self):
        self.bullish = {"SOL": 2}
        self.news_calls = 0

    def rate_news(self, payload):
        self.news_calls += 1
        out = []
        for c in payload["coins"]:
            n = self.bullish.get(c["coin"], 1)
            out.append({"coin": c["coin"], "catalyst": "",
                        "ratings": [{"id": a["id"], "rating": "bullish" if i < n else "neutral"}
                                    for i, a in enumerate(c["articles"])]})
        return {"coins": out}

    def study(self, ctx):
        assert ctx["trade"]["result_r"] is not None
        return {"what_happened": "hit TP", "went_right": ["entry"], "went_wrong": [], "lesson": "ok", "supports": [],
                "new_hypothesis": "Whale L/S > 1.8 with 4-method confluence reaches TP."}

    def thesis_check(self, payload):
        return {"thesis_breaking": False, "reason": ""}


@pytest.fixture
def clock(monkeypatch):
    t = [TUE]
    monkeypatch.setattr(time, "time", lambda: t[0])
    return t


@pytest.fixture
def eng(tmp_path, monkeypatch, clock):
    cal = tmp_path / "calendar.toml"
    cal.write_text(CALENDAR)
    cfg = Config(data_dir=str(tmp_path), mode="paper", paper_balance=100.0, calendar_path=str(cal))
    monkeypatch.setattr(engine_mod, "build_levels", levels)
    monkeypatch.setattr(engine_mod.mx, "rv_percentile", lambda *a: (60.0, 50.0))  # NORMAL volatility
    e = Engine.__new__(Engine)
    e.cfg, e.P = cfg, PROTOCOL
    e.mkt = FakeMarket(Store(cfg.data_dir, "market.db"))
    e.live = False
    e.store = Store(cfg.data_dir, "paper.db")
    e.trader = PaperTrader(cfg, e.mkt, e.store)
    e.analyst = FakeAnalyst()
    e._init_state()
    # OI history HEVI would have recorded itself: OI +2% over 24h and +25% over 30d
    with e.mkt.store._lock:
        e.mkt.store._db.executemany("INSERT INTO oi(coin,ts,oi_usd,px) VALUES(?,?,?,?)",
                                    [(c, TUE - 86400, 49e6, 100) for c in COINS]
                                    + [(c, TUE - 30 * 86400, 40e6, 100) for c in COINS])
        e.mkt.store._db.commit()
    return e


def manual(e):
    """Manual-mode tests: autopilot paused, the user types y."""
    e.set_paused(True)
    return e


def sync(e):
    e.trader._last_step = 0
    return e.manage() + e.sync()


def test_full_flow_start_card_confirm_fill_tp(eng, clock):
    manual(eng)
    res = eng.start(100.0)
    assert isinstance(res, Proposal), res
    p = res.plans[0]
    assert p.coin == "SOL" and p.side == "long"
    text = res.text
    for s in ("HEVI", "Protocol v3.2", "PAPER", "post-only (ALO)", "5m confluence BB+FIB+VP+VWAP", "Isolated",
              "mark-price trigger", "Type \"y\" to confirm", "BTC gate: +1.01%"):
        assert s in text, s
    z = p.sizing
    assert p.entry == 99.61 and z.sl_pct == pytest.approx(1.5, abs=0.01) and p.tp == 105.78  # 106 x 0.998 rounded toward entry
    assert z.leverage == 16 and z.net_rr >= 3 and z.loss_at_sl == pytest.approx(10.0, rel=0.05)
    assert "DOGE" not in text and "USDE" not in text

    msg = eng.confirm(None)
    assert isinstance(msg, str) and msg.startswith("[PAPER] Placed #1"), msg
    t = eng.store.trades("pending")[0]
    assert t["version"] == "3.2" and t["score"] == p.score and t["btc_band"] == "normal"

    # a second start while an entry rests: max 1 position
    assert "already open" in eng.start(100.0)

    clock[0] += 600
    eng.mkt.mark["SOL"] = 99.61  # trades down to the entry
    msgs = sync(eng)
    assert any("entry filled" in m for m in msgs), msgs
    t = eng.store.trades("open")[0]
    assert t["fill_px"] == 99.61 and t["risk_usd"] == pytest.approx(z.loss_at_sl, rel=1e-3)

    clock[0] += 3600
    eng.mkt.mark["SOL"] = p.tp
    msgs = sync(eng)
    assert any("CLOSED (tp)" in m and "New hypothesis #1" in m for m in msgs), msgs
    assert not any("remainder" in m for m in msgs), "a fully filled entry has no remainder to cancel"
    t = eng.store.closed()[0]
    assert t["exit_type"] == "tp" and t["result_r"] == pytest.approx(z.net_rr, abs=0.15)
    assert t["funding"] > 0  # the hour before the TP is charged
    acct = eng.trader.account()
    assert acct["account_value"] - 100.0 == pytest.approx(t["pnl"])
    rv = eng.review()
    assert "Trades 1 | hit rate 100%" in rv
    assert "story" not in eng.how_is_it_going()


def test_reissue_when_price_moves(eng, clock):
    manual(eng)
    res = eng.start(None)
    assert isinstance(res, Proposal)
    eng.mkt.mark["SOL"] = 100.4  # +0.4% > 0.3%: the card must be re-issued, not placed
    out = eng.confirm(None)
    assert isinstance(out, Proposal) and out.text.startswith("Re-issued: price moved 0.40%")
    assert not eng.store.trades("pending")


def test_soft_block_then_override(eng):
    manual(eng)
    eng.analyst.bullish["SOL"] = 1  # news 1/3 bullish fails the (soft) news filter
    res = eng.start(None)
    assert isinstance(res, str) and "No trade today" in res and "override: [your reason]" in res
    assert "M3 news" in res
    prop = eng.override("read the articles myself, they are fine")
    assert isinstance(prop, Proposal) and "OVERRIDE: M3 news" in prop.text
    msg = eng.confirm(None)
    assert msg.startswith("[PAPER] Placed #1"), msg
    assert eng.store.trades("pending")[0]["override"].startswith("read the articles")
    assert eng.store.last_event("override")


def test_hard_rules_cannot_be_overridden(eng, clock):
    clock[0] = datetime(2026, 11, 13, 9, tzinfo=timezone.utc).timestamp()  # Friday
    res = eng.start(None)
    assert "no entries Friday to Sunday" in res
    assert "Nothing to override" in eng.override("I really want it")

    clock[0] = TUE
    eng.mkt.mark["BTC"] = 95.0  # BTC -4.04% from the UTC open
    assert "BTC -4% or worse" in eng.start(None)


def test_unlock_check_is_off_unless_enabled(eng, tmp_path):
    cal = tmp_path / "calendar.toml"
    cal.write_text(CALENDAR.replace('[unlocks]\ncovered_until = "2027-12-31T00:00:00Z"', '[unlocks]\ncovered_until = ""'))
    manual(eng)
    assert isinstance(eng.start(None), Proposal)  # v3.1 default: no unlock data needed
    eng.pending = None
    eng.P = dataclasses.replace(PROTOCOL, screener=dataclasses.replace(PROTOCOL.screener, unlock_check=True))
    eng._init_state()
    res = eng.start(None)
    assert "no coin passes" in res and "token unlock" in res


def test_macro_feed_blocks_a_hold_over_cpi(eng):
    manual(eng)
    eng.mkt.macro, eng.mkt.macro_cov = [("CPI", TUE + 20 * 3600)], True
    res = eng.start(None)
    assert isinstance(res, str) and "M0 macro release" in res


def test_breaker_after_sl_and_resweep_limit(eng, clock):
    clock[0] += 3600  # paper breakers count trades closed since the paper account was (re)set
    eng.store.add_trade(coin="ETH", side="long", status="closed", entry=100, sl=98, tp=106, qty=1, leverage=10,
                        margin=10, created_at=TUE + 600, closed_at=TUE + 1800, exit_type="sl", pnl=-2, result_r=-1)
    assert "SL hit today" in eng.start(None)
    clock[0] += 86400  # next UTC day
    eng.analyst.bullish["SOL"] = 0
    assert "No trade today" in eng.start(None)
    for _ in range(2):
        assert isinstance(eng.resweep(), str)
    assert "Max re-sweeps reached" in eng.resweep()


def test_time_stop_and_friday_flatten(eng, clock):
    manual(eng)
    res = eng.start(None)
    eng.confirm(None)
    clock[0] += 60
    eng.mkt.mark["SOL"] = 99.61
    sync(eng)
    clock[0] += 48 * 3600 + 60  # Thursday 12:01 -> 48h time stop
    msgs = sync(eng)
    assert any("48h time stop" in m for m in msgs) and any("CLOSED (time)" in m for m in msgs), msgs
    assert eng.store.closed()[0]["exit_type"] == "time"
    assert isinstance(res, Proposal)


def test_cancel_unfilled_entry_after_4h(eng, clock):
    manual(eng)
    eng.start(None)
    eng.confirm(None)
    clock[0] += 4 * 3600 + 1
    msgs = sync(eng)
    assert any("entry cancelled" in m and "4h passed" in m for m in msgs), msgs
    assert eng.store.trades("cancelled")


# ---------------------------------------------------------------- autopilot (v3.1)
def test_autopilot_places_without_y_then_waits(eng, clock):
    eng._started = TUE - 3600  # past the warm-up
    msgs = eng.autopilot_tick()
    assert len(msgs) == 1 and "HEVI autopilot: every rule passed" in msgs[0] and "Placed #1" in msgs[0], msgs
    assert "Type \"y\"" not in msgs[0]
    assert eng.pending is None and eng.store.trades("pending")
    clock[0] += 3600
    assert eng.autopilot_tick() == []  # an entry is resting: max 1, no new scan
    assert len(eng.store.trades(limit=10)) == 1


def test_autopilot_waits_for_warmup_interval_window_and_pause(eng, clock):
    assert eng.autopilot_tick() == [] and eng._scans["n"] == 0  # warm-up after (re)start
    eng._started = TUE - 3600
    eng.analyst.bullish["SOL"] = 1  # nothing passes
    assert eng.autopilot_tick() == [] and eng._scans["n"] == 1 and "No trade today" in eng._scans["last"]
    clock[0] += 600
    eng.autopilot_tick()
    assert eng._scans["n"] == 1  # next scan only after 30 min
    eng.set_paused(True)
    clock[0] += 3600
    eng.autopilot_tick()
    assert eng._scans["n"] == 1
    eng.set_paused(False)
    clock[0] = datetime(2026, 11, 13, 10, tzinfo=timezone.utc).timestamp()  # Friday: no entries
    eng.autopilot_tick()
    assert eng._scans["n"] == 0 or eng._scans["n"] == 1  # new UTC day resets the counter; no scan either way
    assert not eng.store.trades("pending")


def test_autopilot_never_overrides(eng):
    eng._started = TUE - 3600
    eng.analyst.bullish["SOL"] = 1  # soft block only - a human could override, the autopilot doesn't
    assert eng.autopilot_tick() == []
    assert not eng.store.trades(limit=5)


def test_claude_reads_news_only_when_it_matters(eng, clock):
    eng._started = TUE - 3600
    real = eng.mkt.whales
    eng.mkt.whales = lambda: {c: {"ratio": 1.0, "holders": 12, "accounts_scanned": 60} for c in COINS}
    eng.autopilot_tick()  # every coin now fails a rule other than news: Claude isn't called at all
    assert eng.analyst.news_calls == 0 and eng._scans["n"] == 1 and not eng.store.trades(limit=5)
    eng.mkt.whales = real
    clock[0] += 31 * 60
    msgs = eng.autopilot_tick()  # SOL passes everything else -> its headlines are rated, then it trades
    assert eng.analyst.news_calls == 1 and any("Placed #1" in m for m in msgs)


def test_autopilot_acts_on_objective_early_exit(eng, clock):
    eng._started = TUE - 3600
    eng.autopilot_tick()
    clock[0] += 60
    eng.mkt.mark["SOL"] = 99.61
    sync(eng)
    assert eng.store.trades("open")
    eng.mkt.mark["BTC"] = 95.0  # BTC -4.04% from the UTC open: objective exit for longs
    msgs = sync(eng)
    assert any("HEVI closed #1 SOL early" in m for m in msgs), msgs
    assert any("CLOSED (early)" in m for m in msgs), msgs
    assert eng.store.closed()[0]["exit_type"] == "early"


def test_daily_report_once_a_day(eng, clock):
    clock[0] = datetime(2026, 11, 10, 21, 5, tzinfo=timezone.utc).timestamp()
    eng._started = clock[0]  # in warm-up: only the report
    msgs = eng.autopilot_tick()
    assert len(msgs) == 1 and msgs[0].startswith("HEVI daily report") and "Mode: PAPER" in msgs[0]
    assert eng.autopilot_tick() == []


def test_prove_mode_goes_live_once_the_paper_record_earns_it(eng, clock, monkeypatch):
    import hlbot.trader as trader_mod

    class FakeLive:
        is_paper = False

        def __init__(self, cfg, mkt):
            self.cfg = cfg

    monkeypatch.setattr(trader_mod, "Trader", FakeLive)
    eng.cfg = dataclasses.replace(eng.cfg, mode="prove")
    eng._started = clock[0] = TUE + 3600
    for i in range(20):
        eng.store.add_trade(coin="ETH", side="long", status="closed", entry=100, sl=98, tp=106, qty=1, leverage=10,
                            margin=10, created_at=TUE + i, closed_at=TUE + 60 + i, exit_type="tp" if i % 3 else "sl",
                            pnl=6 if i % 3 else -2, result_r=3.0 if i % 3 else -1.0)
    msgs = eng._maybe_graduate(clock[0])
    assert "Add HL_ACCOUNT_ADDRESS" in msgs[0] and not eng.live  # passed, but no wallet configured
    eng.cfg = dataclasses.replace(eng.cfg, hl_account_address="0xabc", hl_api_private_key="0x" + "1" * 64)
    msgs = eng._maybe_graduate(clock[0])
    assert eng.live and "going LIVE" in msgs[0] and eng.store.path.endswith("bot.db")
    assert eng.mkt.store.get("graduated")["why"].startswith("20 paper trades")


def test_status_says_when_the_next_scan_is(eng, clock):
    eng._started = TUE - 3600
    text = eng.status()
    assert "Next scan: Tue 10 Nov 12:00 UTC (every 30 min)" in text and "Go-live gate" not in text
    assert "Paper only" in text and "Market history" not in text  # fixture has 30 days of OI history
    clock[0] = datetime(2026, 11, 7, 6, 4, tzinfo=timezone.utc).timestamp()  # a Saturday
    eng._started = clock[0]
    with eng.mkt.store._lock:
        eng.mkt.store._db.execute("DELETE FROM oi")
        eng.mkt.store._db.execute("INSERT INTO oi(coin,ts,oi_usd,px) VALUES('SOL',?,1,1)", (clock[0] - 6 * 3600,))
        eng.mkt.store._db.commit()
    text = eng.status()
    assert "Next scan: Mon 09 Nov 00:00 UTC (entries are Mon-Thu UTC; none on Fri-Sun)" in text
    assert "Market history: 6h of the 24h needed" in text and "Sun 00:04 UTC" in text


def test_absent_whale_blocks_swing_but_day_trades_it(eng, clock):
    eng.mkt.absent = {"SOL"}   # no top-60 leaderboard account holds SOL
    eng.mkt.adr_rng = 7.0      # day wants the target inside one day's range (1.0 x ADR)
    eng.store.set("strategy", "swing")
    res = eng.start(None)
    assert isinstance(res, str) and "M3 whale L/S" in res
    eng.store.set("strategy", "day")
    res = eng.start(None)
    assert isinstance(res, Proposal), res
    p = res.plans[0]
    assert p.coin == "SOL" and p.strategy == "day" and p.hold_h == 24
    assert "[day]" in res.text and "Protocol v3.2" in res.text


def test_multi_uses_day_only_when_btc_is_fast_or_volatile(eng):
    assert eng._pick_mode({"change": 2.5, "vol_now": "NORMAL"})[0] == "day"
    assert eng._pick_mode({"change": -2.2, "vol_now": "NORMAL"})[0] == "day"
    assert eng._pick_mode({"change": 0.4, "vol_now": "HIGH"})[0] == "day"
    assert eng._pick_mode({"change": 0.4, "vol_now": "NORMAL"})[0] == "swing"
    eng.store.set("strategy", "swing")
    assert eng._pick_mode({"change": 5.0, "vol_now": "HIGH"})[0] == "swing"


def test_scan_report_names_the_blocker(eng, clock):
    eng.mkt.absent = {"SOL"}
    eng.store.set("strategy", "swing")
    eng.start(None)
    assert eng._scans["blockers"]["M3 whale L/S"] >= 1
    assert "candidates" in eng._scans["funnel"] or "screened" in eng._scans["funnel"]
    assert "Why no trade" in eng.daily_report() and "Strategy: swing" in eng.daily_report()
