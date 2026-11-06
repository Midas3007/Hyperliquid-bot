"""Paper trading: post-only entries, fill rules, conservative tie-breaks, funding, liquidation, persistence."""

import pytest

from hlbot import paper as paper_mod
from hlbot.config import Config
from hlbot.paper import PaperTrader
from hlbot.store import Store

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
        return {c: {"coin": c, "mark": m, "max_leverage": 20, "margin_tiers": [(0.0, 20)], "funding_1h_pct": self.funding}
                for c, m in self.marks.items()}

    def candles_range(self, coin, interval, s, e):
        return [b for b in self.bars if s <= b["t"] <= e]


def bar(t_ms, h, lo):
    return {"t": int(t_ms), "o": lo, "h": h, "l": lo, "c": (h + lo) / 2, "v": 1}


@pytest.fixture
def pt(tmp_path, monkeypatch):
    clock = Clock(T0)
    monkeypatch.setattr(paper_mod, "time", clock)
    cfg = Config(data_dir=str(tmp_path), paper=True, paper_balance=100.0)  # fees + 0.05% slippage from protocol.toml
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


def test_marketable_entry_rejected_like_alo(pt):
    with pytest.raises(RuntimeError, match="Post only"):
        pt.place_bracket("SOL", "short", 1.0, 99.0, 102.0, 90.0, 10)  # short limit below mark would cross
    with pytest.raises(RuntimeError, match="Post only"):
        pt.place_bracket("SOL", "long", 1.0, 101.0, 97.0, 110.0, 10)
    assert pt.state["orders"] == [] and pt.state["cash"] == 100.0


def fill_at(pt, px):
    """Resting long at `px` (mark 100), then price trades down to it."""
    pt.place_bracket("SOL", "long", 1.0, px, 97.0, 110.0, 10)
    advance(pt, 30, mark=px)
    assert "SOL" in pt.state["positions"]


def test_cancel_all_strips_stops_then_liquidation_caps_loss(pt):
    fill_at(pt, 99.0)
    assert {o["orderType"] for o in pt.open_orders()} == {"Stop Market", "Take Profit Market"}
    assert pt.cancel_all() == 2 and pt.open_orders() == []
    pt.protect_position("SOL", "long", 1.0, 97.0, 110.0)
    assert len(pt.open_orders()) == 2
    pt.cancel_all()

    margin = pt.state["positions"]["SOL"]["margin"]
    before = pt.state["cash"]
    pt.mkt.bars = [bar(int(pt.clock.t * 1000) + M, 100, 80)]
    advance(pt, 120, mark=82)
    assert not pt.state["positions"]
    assert pt.state["cash"] == pytest.approx(before)  # margin gone, nothing returned
    last = pt.fills_since(0)[-1]
    assert float(last["closedPnl"]) == pytest.approx(-margin) and last["liquidation"]


def test_manual_close_add_margin_and_insufficient_margin(pt):
    with pytest.raises(RuntimeError, match="insufficient margin"):
        pt.place_bracket("SOL", "long", 50.0, 99.0, 97.0, 105.0, 10)  # $495 margin on a $100 account
    fill_at(pt, 99.0)
    liq0 = pt.state["positions"]["SOL"]["liq"]
    pt.add_margin("SOL", 5.0)
    assert pt.state["positions"]["SOL"]["liq"] < liq0 and pt.state["positions"]["SOL"]["margin"] == pytest.approx(14.9)
    pt.mkt.marks["SOL"] = 102.0
    pt.close("SOL")
    assert not pt.state["positions"] and pt.open_orders() == []
    with pytest.raises(RuntimeError, match="no open SOL"):
        pt.close("SOL")


def test_funding_accrues_for_longs_and_is_reported_separately(pt):
    pt.mkt.funding = 0.01  # %/h
    fill_at(pt, 99.0)
    advance(pt, 3600 * 10, mark=99.0)
    assert pt.state["positions"]["SOL"]["funding"] == pytest.approx(0.01 / 100 * 99 * 10)
    assert pt.funding_paid("SOL", 0) == pytest.approx(0.099)
    pt.close("SOL")
    close = pt.fills_since(0)[-1]
    assert float(close["closedPnl"]) == pytest.approx(99 * (1 - 0.0005) - 99)  # price PnL only, like Hyperliquid


def test_state_survives_restart(pt):
    pt.place_bracket("SOL", "long", 1.0, 99.0, 97.0, 105.0, 10)
    again = PaperTrader(pt.cfg, pt.mkt, pt.store)
    assert again.state["orders"][0]["entry"] == 99.0
    again.reset(250)
    assert PaperTrader(pt.cfg, pt.mkt, pt.store).state["cash"] == 250
