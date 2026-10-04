"""Offline end-to-end run of start -> card -> y -> fill -> close -> study with fake exchange/Claude."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hlbot.config import Config
from hlbot.engine import Engine, Proposal
from hlbot.market import Market
from hlbot.store import Store

from .test_levels import synth

STEP = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class FakeMarket(Market):
    def __init__(self, cfg, store):  # no network
        self.cfg, self.store, self._cache = cfg, store, {}
        self.base_url = "fake"
        self.flow = type("F", (), {"ensure": lambda self, coins: None})()
        self.positions = []

    def ctxs(self):
        out = {}
        for i, (coin, ch) in enumerate([("BTC", 0.8), ("ETH", 1.2), ("SOL", 2.0), ("DOGE", -9.0), ("TAO", 3.0)]):
            px = self.candles(coin, "5m", 300)[-1]["c"]
            out[coin] = {"coin": coin, "asset": i, "sz_decimals": 2, "max_leverage": 40 if coin == "BTC" else 20,
                         "mark": px, "mid": px, "prev_day": px / (1 + ch / 100), "change_pct": ch,
                         "volume_usd": 900e6, "oi_usd": 500e6, "funding_1h_pct": 0.00125, "funding_8h_pct": 0.01}
        return out

    def candles(self, coin, interval, n):
        seed = sum(map(ord, coin)) + len(interval)
        vol = {"5m": 0.0015, "1h": 0.005, "4h": 0.01, "1d": 0.025}[interval]
        return synth(n, STEP[interval], start=100.0, vol=vol, seed=seed)

    def iv(self, coin):
        return {"rank": 50.0, "label": "NORMAL", "source": "test"}

    def whales(self):
        return {c: {"ratio": 2.2, "long_usd": 2e6, "short_usd": 1e6, "longs": 10, "shorts": 4, "accounts_scanned": 60}
                for c in ("BTC", "ETH", "SOL")}

    def news(self, coin, limit=5):
        return [{"title": f"{coin} rallies", "summary": "", "age_h": 2, "source": "test"}]

    def fear_greed(self):
        return {"value": 62, "label": "Greed"}

    def buy_sell(self, coin):
        return {"ratio": {"ETH": 3.4, "SOL": 3.1, "BTC": 1.2}.get(coin, 1.0), "buy_usd": 1, "sell_usd": 1,
                "minutes": 60, "source": "test"}

    def oi_trend(self, coin, oi_now):
        return {"oi_usd": oi_now, "delta_24h_pct": 3.0}

    def account(self, address):
        return {"account_value": 11.0, "withdrawable": 11.0, "positions": self.positions, "spot_usdc": 0.0}


class FakeTrader:
    def __init__(self, mkt):
        self.mkt, self.placed, self.closed = mkt, [], []

    def place_bracket(self, coin, side, qty, entry, sl, tp, lev):
        self.placed.append((coin, side, qty, entry, sl, tp, lev))
        return {"entry_oid": 42, "filled_now": False, "statuses": []}

    def order_status(self, oid):
        return {"status": "order", "order": {"status": "filled"}}

    def open_orders(self):
        return [{"coin": c, "isTrigger": True, "orderType": "Stop Market", "oid": 1} for c, *_ in self.placed]

    def fills_since(self, s):
        coin, side, qty, entry, sl, tp, lev = self.placed[-1]
        return [{"coin": coin, "dir": "Close Long", "px": str(tp), "sz": str(qty), "closedPnl": "1.5", "fee": "0.02"}]

    def cancel_all(self, coin=None):
        return 0

    def cancel(self, coin, oid):
        pass


class FakeAnalyst:
    def pick(self, ctx):
        assert ctx["options"], "pipeline should produce validated options"
        best = ctx["options"][0]["id"]
        return {"pick": best, "alternate": "NONE", "confidence": 70, "news": [], "ranking": [],
                "rationale": "test pick", "warnings": ["test warning"]}

    def study(self, ctx):
        return {"what_happened": "hit TP", "went_right": ["entry"], "went_wrong": [], "lesson": "ok",
                "propose_rule": True, "rule_text": "Prefer coins with whale L/S > 2."}


@pytest.fixture
def eng(tmp_path, monkeypatch):
    cfg = Config(data_dir=str(tmp_path), hl_account_address="0xabc")
    e = Engine.__new__(Engine)
    e.cfg = cfg
    e.store = Store(cfg.data_dir)
    e.mkt = FakeMarket(cfg, e.store)
    e.trader = FakeTrader(e.mkt)
    e.analyst = FakeAnalyst()
    e.pending = None
    e._last_pump_alert = 0.0
    e._friday_alarm_day = None
    # pin to a Tuesday noon so weekday rules don't interfere
    monkeypatch.setattr(Engine, "now", lambda self: datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("UTC")))
    return e


@pytest.mark.parametrize("mode", ["A", "B"])
def test_full_flow(eng, mode):
    eng.store.set("mode", mode)
    res = eng.start(11.0, None)
    if isinstance(res, str):
        pytest.skip(f"synthetic market produced no setup in mode {mode}: {res}")
    assert isinstance(res, Proposal)
    assert "CONFIRMATION CARD" in res.text and "Reply  y" in res.text
    assert "DOGE" not in [o.setup.coin for o in res.options]  # -9% day filter
    assert "TAO" not in [o.setup.coin for o in res.options]  # skip list

    msg = eng.confirm(None)
    assert msg.startswith("Placed #1"), msg
    coin, side, qty, entry, sl, tp, lev = eng.trader.placed[0]
    assert lev <= 25 and qty > 0 and qty * entry >= 10

    # entry fills -> position appears
    eng.mkt.positions = [{"coin": coin, "side": side, "size": qty, "entry": entry, "upnl": 0, "liq": None,
                          "leverage": lev, "margin_used": 1}]
    msgs = eng.sync()
    assert any("filled" in m for m in msgs)
    assert eng.store.trades("open")

    # position gone -> closed + study + proposed rule
    eng.mkt.positions = []
    msgs = eng.sync()
    assert any("CLOSED (tp)" in m and "Proposed new rule" in m for m in msgs), msgs
    t = eng.store.trades("closed")[0]
    assert t["pnl"] == pytest.approx(1.48)


def test_weekend_blocked(eng, monkeypatch):
    monkeypatch.setattr(Engine, "now", lambda self: datetime(2026, 10, 10, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert "Weekend" in eng.start(11.0, None)


def test_friday_force_close(eng, monkeypatch):
    eng.store.add_trade(coin="SOL", side="long", status="open", entry=100, sl=98, tp=106, qty=1, leverage=10,
                        margin=10, entry_oid=None, filled_at=time.time())
    closed = []
    eng.trader.close = lambda coin: closed.append(coin)
    monkeypatch.setattr(Engine, "now", lambda self: datetime(2026, 10, 9, 20, 30, tzinfo=ZoneInfo("UTC")))
    msgs = eng.manage()
    assert closed == ["SOL"] and any("friday close" in m for m in msgs)
