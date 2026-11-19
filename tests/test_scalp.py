"""Scalp mode (scalp-0.1): sizing, liquidation buffer, honest fills, break-even after TP1, breakers, gate."""

import json
import time
from datetime import datetime, timezone

from hlbot import scalp as sc
from hlbot.config import Config
from hlbot.protocol import PROTOCOL as P

S = P.scalp
TIERS = [(0.0, 40)]


def test_size_derives_leverage_and_checks_liquidation():
    z = sc.size("long", 100_000, 99_750, 100.0, 1.0, 0.1, TIERS, 5, S)
    assert z.ok and z.leverage <= S.max_leverage and z.net_rr >= S.min_net_rr
    assert abs(z.risk_usd - 1.0) < 0.05                                    # 1% of $100 at the stop, costs included
    # targets in net R: TP1 pays 1x and TP2 2x what the stop costs, after all fees
    loss = z.notional * ((100_000 - z.stop) / 100_000 + (S.maker_pct + S.taker_pct + S.slippage_pct) / 100)
    gain = lambda px: z.qty * (px - 100_000) - z.notional * 2 * S.maker_pct / 100  # noqa: E731
    assert abs(gain(z.tp1) / loss - 1.0) < 0.01 and abs(gain(z.tp2) / loss - 2.0) < 0.01 and z.net_rr >= 1.0
    assert (100_000 - z.liq) / 100_000 >= S.liq_buffer_mult * (100_000 - z.stop) / 100_000
    assert not sc.size("long", 100_000, 99_000, 100.0, 1.0, 0.1, TIERS, 5, S).ok          # 1% stop > 0.60% band
    z = sc.size("long", 100_000, 99_950, 100.0, 1.0, 0.1, TIERS, 5, S)                      # 0.05% widened to the 0.20% floor
    assert z.ok and abs((100_000 - z.stop) / 1000 - 0.20) < 1e-6
    bad = sc.size("long", 100_000, 99_750, 100.0, 4.0, 0.1, [(0.0, 3)], 5, S)               # 3x coin: no room for 3x buffer
    assert not bad.ok


class Mkt:
    def __init__(self, c):
        self.c, self.t = c, 0

    def candles(self, coin, iv, n):
        return [x for x in self.c if x["t"] + 60_000 <= self.t][-n:]

    def ctxs(self):
        return {"BTC": {"margin_tiers": TIERS, "sz_decimals": 5}}

    def macro_calendar(self, names):
        return [], None


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": 1.0}


def eng_with(tmp_path, candles):
    m = Mkt(candles)
    e = sc.ScalpEngine(Config(data_dir=str(tmp_path)), m)
    return e, m


def test_fill_needs_trade_through_then_tp1_moves_stop_to_breakeven(tmp_path, monkeypatch):
    t0 = int(datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    c = [bar(t0 + i * 60_000, 100_000, 100_010, 99_990, 100_000) for i in range(3)]
    e, m = eng_with(tmp_path, c)
    monkeypatch.setattr(sc, "find_signal", lambda c1, s: (sc.Signal("B pullback", "long", 100_000, 99_750, "test"), "ok"))
    m.t = c[-1]["t"] + 60_001
    e.st["last_t"] = c[-2]["t"]
    msgs = e.tick(m.t / 1000)
    assert any("post-only 100,000.0" in x for x in msgs), msgs
    tid = e.store.trades("pending")[0]["id"]
    monkeypatch.setattr(sc, "find_signal", lambda c1, s: (None, "none"))
    # a candle that only TOUCHES the limit does not fill
    c.append(bar(c[-1]["t"] + 60_000, 100_050, 100_060, 100_000, 100_040)); m.t = c[-1]["t"] + 60_001
    e.tick(m.t / 1000)
    assert e.store.trade(tid)["status"] == "pending"
    # trades through -> filled
    c.append(bar(c[-1]["t"] + 60_000, 100_040, 100_045, 99_980, 100_010)); m.t = c[-1]["t"] + 60_001
    e.tick(m.t / 1000)
    t = e.store.trade(tid)
    assert t["status"] == "open"
    plan = json.loads(t["plan"])
    # TP1 next candle -> half off, stop to break-even + costs
    c.append(bar(c[-1]["t"] + 60_000, 100_010, plan["tp1"] + 5, 100_000, plan["tp1"])); m.t = c[-1]["t"] + 60_001
    msgs = e.tick(m.t / 1000)
    t = e.store.trade(tid)
    assert any("TP1 hit" in x for x in msgs) and t["sl"] > 100_000
    # back to break-even -> closed with a small positive R (half banked at 1R)
    c.append(bar(c[-1]["t"] + 60_000, plan["tp1"], plan["tp1"], 99_990, 99_995)); m.t = c[-1]["t"] + 60_001
    e.tick(m.t / 1000)
    t = e.store.trade(tid)
    assert t["status"] == "closed" and t["exit_type"] == "tp1+be" and 0.45 < t["result_r"] < 0.7
    assert abs(e.equity() - (100 + t["pnl"])) < 1e-9


def test_breakers_and_gate(tmp_path):
    e, _ = eng_with(tmp_path, [])
    now = time.time()
    for i, r in enumerate([-1, -1, -1]):
        e.store.add_trade(coin="BTC", side="long", status="closed", entry=1, sl=1, tp=1, closed_at=now + i, result_r=r, risk_usd=1)
    e.st["cooldown_until"] = now + 600
    risk, block = e.risk_now(now)
    assert any("cooldown" in b for b in block)
    e.st["halted"] = True
    assert any("halted" in b for b in e.risk_now(now)[1])
    assert e.gate()[0] is False and "3/30" in e.gate()[1]
    for i in range(27):
        e.store.add_trade(coin="BTC", side="long", status="closed", entry=1, sl=1, tp=1, closed_at=now + 10 + i, result_r=2.0, risk_usd=1)
    ok, msg = e.gate()
    assert ok and "PASSED" in msg and "live needs your explicit go" in msg   # (3 x -1 + 27 x 2) / 30 = +1.7R


def test_sessions():
    e = sc.ScalpEngine.__new__(sc.ScalpEngine)
    e.s = S
    d = datetime(2026, 11, 19, tzinfo=timezone.utc).timestamp()
    assert e.in_session(d + 8 * 3600)[0] and not e.in_session(d + 12 * 3600)[0] and e.in_session(d + 16.9 * 3600)[0]
    assert not e.in_session(d + 17 * 3600)[0]


def test_daily_summary_format_lagos_day(tmp_path):
    from hlbot.summary import daily_summary
    e, _ = eng_with(tmp_path, [])
    now = datetime(2026, 11, 19, 23, 0, tzinfo=timezone.utc).timestamp()   # 00:00 Lagos on the 20th
    e.st["reset_at"] = now - 86400
    for i, pnl in enumerate([0.05, 0.04, -0.03]):
        e.store.add_trade(coin="BTC", side="long", status="closed", entry=100.0, fill_px=100.0, exit_px=101.0, qty=10,
                          pnl=pnl, result_r=pnl * 10, closed_at=now - 3600 * (3 - i), risk_usd=0.1)

    class Swing:
        live = False
        store = e.store.__class__(str(tmp_path / "swing"), "paper.db")

        def _since(self):
            return 0

    text = daily_summary(Swing(), e, now=now)
    assert "Thu 19 Nov (Lagos time)" in text and "Normal trades (paper):\nToday (Lagos time): no closed trades" in text
    assert "Today (Lagos time): 3 trades, 2 profitable (67%)\nProfit +$0.06 after fees · traded $6,030.00\nLast trade 23:00" in text


def test_arms_leaderboard_ranks_by_interval(tmp_path):
    from hlbot import arms
    e, m = eng_with(tmp_path, [])
    built = arms.build(Config(data_dir=str(tmp_path)), m)
    assert set(built) == set(arms.ARMS) and built["F_3m"].s.signal_tf == 3 and built["I_claude"].s.claude_veto
    now = time.time()
    for i in range(5):
        built["H_quick_1R"].store.add_trade(coin="BTC", side="long", status="closed", entry=1, sl=1, tp=1, closed_at=now + i,
                                             result_r=1.0, risk_usd=1)
    text = arms.leaderboard(e, built)
    assert text.splitlines()[2].startswith("- H_quick_1R: 5 | 100% | +1.00")
