"""v3.3 whale data: the tracked-trader list and the big-order fallback."""

import time

from hlbot.market import FlowCollector, is_zero_hash, pick_whales
from hlbot.protocol import PROTOCOL as P


def row(addr, av, month_pnl, month_vlm, all_pnl):
    return {"ethAddress": addr, "accountValue": str(av),
            "windowPerformances": [["day", {"pnl": "0", "roi": "0", "vlm": "0"}],
                                   ["month", {"pnl": str(month_pnl), "roi": "0", "vlm": str(month_vlm)}],
                                   ["allTime", {"pnl": str(all_pnl), "roi": "0", "vlm": "0"}]]}


def test_pick_whales_keeps_profitable_active_traders_only():
    rows = [
        row("mm", 90e6, 25e6, 90e6 * 500, 19e6),       # market maker: 500x monthly turnover
        row("idle", 120e6, 1e6, 0, 5e6),               # holds, doesn't trade perps
        row("loser", 10e6, -2e6, 50e6, 3e6),           # down this month
        row("faded", 10e6, 2e6, 50e6, -1e6),           # down all-time
        row("small", 50_000, 40_000, 500_000, 90_000),  # under $100k
        row("b", 5e6, 3e6, 60e6, 4e6),
        row("a", 8e6, 9e6, 40e6, 20e6),
        {"ethAddress": "bad", "accountValue": None},    # malformed rows are skipped
    ]
    assert pick_whales(rows, P.deep_dive) == ["a", "b"]  # ranked by this month's profit


def test_pick_whales_caps_the_list():
    rows = [row(f"x{i}", 1e6, 1000 + i, 5e6, 1) for i in range(250)]
    out = pick_whales(rows, P.deep_dive)
    assert len(out) == P.deep_dive.whale_accounts == 200 and out[0] == "x249"


def test_zero_hash():
    assert is_zero_hash("0x" + "0" * 64) and is_zero_hash(None) and is_zero_hash("")
    assert not is_zero_hash("0x00ab")


def trade(coin, side, px, sz, h, tid, t_ms):
    return {"coin": coin, "side": side, "px": str(px), "sz": str(sz), "hash": h, "tid": tid, "time": t_ms}


def test_big_orders_group_fills_by_order_and_need_coverage():
    f = FlowCollector("x", 60, 30, 15, big_window_min=240, big_floor_usd=10_000)
    now = time.time()
    f._since["SOL"] = now - 3600
    ms = int(now * 1000)
    zero = "0x" + "0" * 64
    f._on_trades({"data": [
        trade("SOL", "B", 100, 60, "0xaa", 1, ms), trade("SOL", "B", 100, 50, "0xaa", 2, ms),  # one $11k buy order
        trade("SOL", "A", 100, 40, "0xbb", 3, ms),                                            # $4k: small
        trade("SOL", "A", 100, 300, zero, 4, ms), trade("SOL", "A", 100, 200, zero, 5, ms),  # zero hash: 2 orders
    ]})
    for k in f._pending["SOL"]:  # pretend the fills settled
        f._pending["SOL"][k][0] -= 10
    r = f.big_orders("SOL", 0)
    assert r["orders"] == 3 and r["buy_usd"] == 11_000 and r["sell_usd"] == 50_000
    assert abs(r["ratio"] - 0.22) < 1e-9 and r["threshold_usd"] == 10_000
    # a coin-specific threshold above the floor drops the $11k order
    r = f.big_orders("SOL", 15_000)
    assert r["orders"] == 2 and r["buy_usd"] == 0 and r["ratio"] == 0
    # not enough trade history yet -> no reading
    f._since["SOL"] = time.time() - 600
    assert f.big_orders("SOL", 0) is None
    assert f.big_orders("ETH", 0) is None  # never subscribed
