import types

from hlbot import trend as T

H4 = T.H4


def series(prices):
    return [{"t": i * H4, "o": p, "h": p * 1.001, "l": p * 0.999, "c": p} for i, p in enumerate(prices)]


def test_backtest_long_then_cross_back():
    prices = [100 - i * 0.05 for i in range(300)] + [85 + i * 0.5 for i in range(120)] + [145 - i * 0.6 for i in range(150)]
    tr = T.backtest(series(prices))
    assert tr, "expected at least one trade"
    assert tr[0][1] > 0          # the up-leg long made money after costs


class FakeMkt:
    def __init__(self, c):
        self.c = c

    def candles(self, coin, iv, n):
        return self.c


def test_engine_starts_flat_then_trades_new_bars(tmp_path):
    prices = [100 - i * 0.05 for i in range(300)] + [85 + i * 0.5 for i in range(120)]
    c = series(prices)
    mkt = FakeMkt(c[:290])
    eng = T.TrendEngine(types.SimpleNamespace(data_dir=str(tmp_path)), mkt)
    now = lambda k: (c[k]["t"] + H4) / 1000 + 1  # noqa: E731
    assert eng.tick(now(289)) == []                      # first run never trades history
    mkt.c = c
    msgs = eng.tick(now(len(c) - 1))
    assert any("LONG" in m for m in msgs)
    t = eng.store.trades("open")[0]
    assert t["risk_usd"] <= 1.01                         # 1% of $100
    assert "TREND" in eng.status()
