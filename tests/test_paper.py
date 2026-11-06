"""Paper trading: fill rules, conservative tie-breaks, persistence, and the full engine flow on simulated fills."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hlbot import paper as paper_mod
from hlbot.config import Config
from hlbot.engine import Engine, Proposal
from hlbot.paper import PaperTrader
from hlbot.store import Store

from .test_pipeline import FakeAnalyst, FakeMarket

T0 = 1_800_000_000.0  # seconds
M = 60_000


class Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


class PMkt:
    def __init__(self):
        self.marks = {"SOL": 100.0}
        self.bars = []
        self.funding = 0.0

    def ctxs(self):
        return {c: {"coin": c, "mark": m, "max_leverage": 20, "funding_1h_pct": self.funding} for c, m in self.marks.items()}

    def candles_range(self, coin, interval, s, e):
        return [b for b in self.bars if s <= b["t"] <= e]


def bar(t_ms, h, lo):
    return {"t": int(t_ms), "o": lo, "h": h, "l": lo, "c": (h + lo) / 2, "v": 1}


@pytest.fixture
def pt(tmp_path, monkeypatch):
    clock = Clock(T0)
    monkeypatch.setattr(paper_mod, "time", clock)
    cfg = Config(data_dir=str(tmp_path), paper=True, paper_balance=100.0, paper_slippage_pct=0.05)
    store = Store(cfg.data_dir, "paper.db")
    tr = PaperTrader(cfg, PMkt(), store)
    tr.clock = clock
    return tr


def advance(tr, seconds, mark=None):
    tr.clock.t += seconds
    if mark is not None:
        tr.mkt.marks["SOL"] = mark
    tr.step(force=True)


def test_resting_entry_fills_then_tp(pt):
    r = pt.place_bracket("SOL", "long", 1.0, 99.0, 97.0, 105.0, 10)
    assert r["filled_now"] is False and pt.order_status(r["entry_oid"])["order"]["status"] == "open"
    assert pt.account()["account_value"] == pytest.approx(100.0)

    t0 = int(T0 * 1000)
    pt.mkt.bars = [bar(t0 - M, 100, 95)]  # began before the order: must be ignored
    advance(pt, 30)
    assert not pt.state["positions"]

    pt.mkt.bars.append(bar(t0 + M, 100, 98.9))
    advance(pt, 120, mark=99.5)
    assert pt.order_status(r["entry_oid"])["order"]["status"] == "filled"
    pos = pt.account()["positions"][0]
    assert pos["entry"] == 99.0 and pos["size"] == 1.0
    assert pt.state["cash"] == pytest.approx(100 - 9.9 - 99 * 0.00015)

    pt.mkt.bars.append(bar(t0 + 3 * M, 105.2, 99.2))
    advance(pt, 120, mark=104.0)
    assert not pt.state["positions"]
    close = [f for f in pt.fills_since(0) if f["dir"] == "Close Long"][0]
    exit_px = 105 * (1 - 0.0005)
    assert float(close["px"]) == pytest.approx(exit_px)
    pnl = exit_px - 99.0
    fees = 99 * 0.00015 + exit_px * 0.00045
    assert pt.account()["account_value"] == pytest.approx(100 + pnl - fees)


def test_fill_candle_ignores_tp_and_ties_go_to_sl(pt):
    pt.place_bracket("SOL", "long", 1.0, 99.0, 97.0, 105.0, 10)
    t0 = int(T0 * 1000)
    pt.mkt.bars = [bar(t0 + M, 106, 98.5)]  # fills the entry and touches TP in the same minute
    advance(pt, 120, mark=100)
    assert "SOL" in pt.state["positions"], "TP in the fill candle must not count"

    pt.mkt.bars.append(bar(t0 + 3 * M, 106, 96.9))  # touches both SL and TP
    advance(pt, 120, mark=100)
    close = [f for f in pt.fills_since(0) if f["dir"].startswith("Close")][0]
    assert float(close["px"]) == pytest.approx(97 * (1 - 0.0005))
    assert float(close["closedPnl"]) < 0


def test_marketable_entry_fills_at_mark_with_taker_fee(pt):
    r = pt.place_bracket("SOL", "short", 1.0, 99.0, 102.0, 90.0, 10)  # short limit below mark: marketable
    assert r["filled_now"]
    p = pt.state["positions"]["SOL"]
    assert p["entry"] == pytest.approx(100 * (1 - 0.0005))
    assert pt.state["cash"] == pytest.approx(100 - p["entry"] / 10 - p["entry"] * 0.00045)


def test_cancel_all_strips_stops_then_liquidation_caps_loss(pt):
    pt.place_bracket("SOL", "long", 1.0, 101.0, 97.0, 110.0, 10)  # marketable
    assert {o["orderType"] for o in pt.open_orders()} == {"Stop Market", "Take Profit Market"}
    assert pt.cancel_all() == 2 and pt.open_orders() == []
    pt.protect_position("SOL", "long", 1.0, 97.0, 110.0)
    assert len(pt.open_orders()) == 2
    pt.cancel_all()

    margin = pt.state["positions"]["SOL"]["margin"]
    before = pt.state["cash"]
    pt.mkt.bars = [bar(int(T0 * 1000) + M, 100, 80)]
    advance(pt, 120, mark=82)
    assert not pt.state["positions"]
    assert pt.state["cash"] == pytest.approx(before)  # margin gone, nothing returned
    assert float(pt.fills_since(0)[-1]["closedPnl"]) == pytest.approx(-margin)


def test_manual_close_and_insufficient_margin(pt):
    with pytest.raises(RuntimeError, match="insufficient margin"):
        pt.place_bracket("SOL", "long", 50.0, 99.0, 97.0, 105.0, 10)  # $495 margin on a $100 account
    pt.place_bracket("SOL", "long", 1.0, 101.0, 97.0, 110.0, 10)
    pt.mkt.marks["SOL"] = 102.0
    pt.close("SOL")
    assert not pt.state["positions"] and pt.open_orders() == []
    with pytest.raises(RuntimeError, match="no open SOL"):
        pt.close("SOL")


def test_funding_accrues_for_longs(pt):
    pt.mkt.funding = 0.01  # %/h
    pt.place_bracket("SOL", "long", 1.0, 101.0, 90.0, 110.0, 10)
    advance(pt, 3600 * 10, mark=100.0)
    assert pt.state["positions"]["SOL"]["funding"] == pytest.approx(0.01 / 100 * 100 * 10)


def test_state_survives_restart(pt):
    pt.place_bracket("SOL", "long", 1.0, 99.0, 97.0, 105.0, 10)
    again = PaperTrader(pt.cfg, pt.mkt, pt.store)
    assert again.state["orders"][0]["entry"] == 99.0
    again.reset(250)
    assert PaperTrader(pt.cfg, pt.mkt, pt.store).state["cash"] == 250


# ---------------------------------------------------------------- full engine flow on paper fills
class LiveishMarket(FakeMarket):
    """Synthetic market from the pipeline test, with a controllable mark and no candle history."""

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        self.mark_override = {}

    def ctxs(self):
        out = super().ctxs()
        for coin, m in self.mark_override.items():
            out[coin] = dict(out[coin], mark=m, mid=m)
        return out

    def candles_range(self, coin, interval, s, e):
        return []


@pytest.mark.parametrize("mode", ["A", "B"])
def test_engine_flow_on_paper(tmp_path, monkeypatch, mode):
    cfg = Config(data_dir=str(tmp_path), paper=True, paper_balance=11.0)
    e = Engine.__new__(Engine)
    e.cfg = cfg
    e.store = Store(cfg.data_dir, "paper.db")
    e.mkt = LiveishMarket(cfg, e.store)
    e.trader = PaperTrader(cfg, e.mkt, e.store)
    e.analyst = FakeAnalyst()
    e.pending = None
    e._last_pump_alert = 0.0
    e._friday_alarm_day = None
    monkeypatch.setattr(Engine, "now", lambda self: datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("UTC")))
    e.store.set("mode", mode)

    res = e.start(None, None)
    if isinstance(res, str):
        pytest.skip(f"synthetic market produced no setup in mode {mode}: {res}")
    assert isinstance(res, Proposal) and "PAPER TRADE" in res.text
    s = res.options[0].setup
    assert e.confirm(None).startswith("[PAPER] Placed #1")

    e.mkt.mark_override[s.coin] = s.entry  # price trades at the entry -> fill
    e.trader._last_step = 0
    msgs = e.sync()
    assert e.store.trades("open"), msgs

    e.mkt.mark_override[s.coin] = s.tp  # then reaches TP
    e.trader._last_step = 0
    msgs = e.sync()
    assert any("CLOSED (tp)" in m for m in msgs), msgs
    t = e.store.trades("closed")[0]
    acct = e.trader.account()
    assert acct["positions"] == []
    assert t["pnl"] > 0
    assert acct["account_value"] - 11.0 == pytest.approx(t["pnl"])  # journal PnL includes both fees
    assert "PAPER track record" in e.stats() and "Trades 1" in e.stats()
