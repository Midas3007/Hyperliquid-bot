"""Backtest mechanics on synthetic candles: exits, the one-position rule, and no look-ahead."""

from datetime import datetime, timezone

from hlbot import backtest as bt
from hlbot.protocol import PROTOCOL as P
from hlbot.rules.sizing import Fees, SizingInput, size

H = bt.H_MS
FEES = Fees(P.fees.maker_pct, P.fees.taker_pct, P.fees.slippage_pct, "test")


def ms(y, mo, d, h=0):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def hours(start, prices):
    """1h candles from (low, high, close) tuples."""
    return [{"t": start + i * H, "o": c, "h": hi, "l": lo, "c": c, "v": 1.0} for i, (lo, hi, c) in enumerate(prices)]


def sizing(entry=100.0, sl=98.0, tp=107.0):
    return size(SizingInput("long", entry, sl, tp, 100.0, 100.0, 10.0, False, 2, [(0, 20)], FEES, bt.BASE_FUNDING_8H),
                P.risk, P.exchange, P.filters)


def test_walk_tp_sl_same_candle_and_time_stop():
    t0 = ms(2026, 11, 2, 0)  # Monday
    z = sizing()
    assert z.ok
    c = hours(t0, [(99.9, 100.1, 100)] + [(99.5, 101, 100.5)] * 3 + [(100, 107.5, 107)])
    assert bt.walk(c, 0, "long", 100, 98, 107, z, 48, FEES)[1:] == ("tp", z.net_rr, 107)
    c = hours(t0, [(99.9, 100.1, 100), (97.0, 108.0, 100)])  # touches both: the SL counts
    assert bt.walk(c, 0, "long", 100, 98, 107, z, 48, FEES)[1:] == ("sl", -1.0, 98)
    c = hours(t0, [(99.9, 100.1, 100)] + [(99.5, 100.6, 100.5)] * 30)
    end, kind, r, px = bt.walk(c, 0, "long", 100, 98, 107, z, 24, FEES)
    assert kind == "time" and end == t0 + 25 * H and -0.2 < r < 0.3 and px == 100.5
    c = hours(t0, [(99.9, 100.1, 100)] * 10)
    assert bt.walk(c, 0, "long", 100, 98, 107, z, 48, FEES) == (None, "open", None, None)  # data ends first


def test_walk_flattens_on_friday():
    t0 = ms(2026, 11, 5, 18)  # Thursday 18:00 UTC -> Friday 20:00 flat is 26h later
    z = sizing()
    c = hours(t0, [(99.9, 100.1, 100)] + [(99.5, 100.6, 100.2)] * 40)
    end, kind, _, _ = bt.walk(c, 0, "long", 100, 98, 107, z, 48, FEES)
    assert kind == "friday" and end == ms(2026, 11, 6, 20)


def test_one_position_at_a_time_and_day_stop_after_sl():
    S = bt.Setup
    d = ms(2026, 11, 2, 0)
    rows = [S("SOL", d, "long", "v", 1, 1, 1, 3.5, d + 5 * H, "sl", -1.0),
            S("ETH", d + 2 * H, "long", "v", 1, 1, 1, 3.5, d + 9 * H, "tp", 3.5),   # overlaps SOL account-wide
            S("ETH", d + 6 * H, "long", "v", 1, 1, 1, 3.5, d + 9 * H, "tp", 3.5),   # same UTC day as the SL
            S("BTC", d + 30 * H, "long", "v", 1, 1, 1, 3.2, d + 40 * H, "tp", 3.2)]
    acct = bt.one_at_a_time(rows, per_coin=False)
    assert [(s.coin, s.t) for s in acct] == [("SOL", d), ("BTC", d + 30 * H)]
    per = bt.one_at_a_time(rows, per_coin=True)
    assert [s.coin for s in per] == ["SOL", "ETH", "BTC"]  # per coin: ETH's day isn't stopped by SOL
    st = bt.stats(acct)
    assert st["n"] == 2 and st["exp_r"] == (3.2 - 1) / 2 and st["pf"] == 3.2 and st["max_dd_r"] == 1.0


def test_as_of_rebuilds_forming_candles_without_look_ahead():
    t0 = ms(2026, 11, 2, 0)
    c1h = hours(t0, [(100 + i, 101 + i, 100.5 + i) for i in range(30)])
    c4h = [{"t": t0 - 4 * H * k, "o": 1, "h": 2, "l": 0.5, "c": 1, "v": 1} for k in range(200, 0, -1)]
    c4h += [{"t": t0 + 4 * H * k, "o": 9, "h": 999, "l": 0.1, "c": 9, "v": 1} for k in range(8)]  # the "future"
    c1d = [{"t": t0 - 86_400_000 * k, "o": 1, "h": 2, "l": 0.5, "c": 1, "v": 1} for k in range(90, 0, -1)]
    c1d += [{"t": t0, "o": 9, "h": 999, "l": 0.1, "c": 9, "v": 1}]
    ser = bt.Series(c1h, c4h, c1d)
    c4_done, c4, d_done, d_all = ser.as_of(5)  # at 06:00 UTC: the 04:00 block has 2 hours (04:00, 05:00)
    assert c4_done[-1]["t"] == t0 and all(c["t"] < t0 + 4 * H for c in c4_done)  # nothing from the future
    assert c4[-1] == {"t": t0 + 4 * H, "o": 104.5, "h": 106, "l": 104, "c": 105.5, "v": 2.0}
    assert d_done[-1]["t"] == t0 - 86_400_000 and max(c["h"] for c in d_done) == 2
    assert d_all[-1] == {"t": t0, "o": 100.5, "h": 106, "l": 100, "c": 105.5, "v": 6.0}  # today, rebuilt from hours
