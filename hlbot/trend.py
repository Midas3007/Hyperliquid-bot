"""4h EMA trend arm (paper only). The closest thing to an edge in docs/RESEARCH.md, now run forward on real time.

Rules (fixed before any forward data, same as scripts/research_trend.py):
  * BTC, ETH, SOL on Hyperliquid 4h candles, long and short, one position per coin.
  * Enter at the close of the 4h bar where EMA10 crosses EMA200.
  * Initial stop 2x ATR14 from the entry; exit at the stop or at the close of the bar where the EMAs cross back.
  * Costs: taker 0.045% + 0.02% slippage each way, funding 0.01% per 8h against the position.
  * Size: 1% of the arm's equity at risk per trade, notional capped at 3x equity per position.
Own $100 ledger in DATA_DIR/trend. It never trades real money and is not part of any go-live gate; it is only here
to find out whether the backtest edge survives forward.
"""

from __future__ import annotations

import os
import time

from .scalp import scorecard
from .store import Store

COINS = ("BTC", "ETH", "SOL")
FAST, SLOW, ATR_N, ATR_MULT = 10, 200, 14, 2.0
CE, FUND_8H = 0.065, 0.01            # % of notional per side; % per 8h
RISK_PCT, MAX_NOTIONAL_X, BALANCE = 1.0, 3.0, 100.0
H4 = 4 * 3_600_000
BARS = 1000                          # EMA200 warm-up: 5x the period


def ema(vals: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def atr(c: list[dict], i: int, n: int = ATR_N) -> float:
    return sum(max(c[k]["h"] - c[k]["l"], abs(c[k]["h"] - c[k - 1]["c"]), abs(c[k]["l"] - c[k - 1]["c"]))
               for k in range(i - n + 1, i + 1)) / n


def step(pos: dict | None, c: list[dict], i: int, ef: list[float], es: list[float]):
    """One completed bar. -> ("exit", price, kind) | ("enter", side, entry, stop) | None."""
    x = c[i]
    if pos:
        s = 1 if pos["side"] == "long" else -1
        if (x["l"] <= pos["sl"]) if s > 0 else (x["h"] >= pos["sl"]):
            return ("exit", pos["sl"], "sl")
        if (ef[i] < es[i]) if s > 0 else (ef[i] > es[i]):
            return ("exit", x["c"], "cross")
        return None
    up = ef[i] > es[i] and ef[i - 1] <= es[i - 1]
    dn = ef[i] < es[i] and ef[i - 1] >= es[i - 1]
    if up or dn:
        s = 1 if up else -1
        return ("enter", "long" if up else "short", x["c"], x["c"] - s * ATR_MULT * atr(c, i))
    return None


def net_r(side: str, entry: float, stop: float, px: float, held_h: float) -> float:
    s = 1 if side == "long" else -1
    risk = abs(entry - stop) / entry * 100 + 2 * CE
    return (s * (px - entry) / entry * 100 - 2 * CE - FUND_8H * held_h / 8) / risk


def backtest(c: list[dict]) -> list[tuple[int, float]]:
    """Whole-series replay with the same step(): [(entry_ms, R), ...]."""
    closes = [x["c"] for x in c]
    ef, es = ema(closes, FAST), ema(closes, SLOW)
    out, pos = [], None
    for i in range(SLOW + 2, len(c)):
        a = step(pos, c, i, ef, es)
        if a and a[0] == "exit":
            out.append((pos["t"], net_r(pos["side"], pos["entry"], pos["sl0"], a[1], (c[i]["t"] - pos["t"]) / 3.6e6)))
            pos = None
        elif a and a[0] == "enter":
            pos = {"side": a[1], "entry": a[2], "sl": a[3], "sl0": a[3], "t": c[i]["t"]}
    return out


class TrendEngine:
    name = "trend"

    def __init__(self, cfg, mkt):
        self.mkt = mkt
        self.store = Store(os.path.join(cfg.data_dir, "trend"), "trend.db")
        st = self.store.get("trend_state")
        if not st:
            st = {"cash": BALANCE, "start": BALANCE, "reset_at": time.time(), "last_t": {}}
            self.store.set("trend_state", st)
            self.store.record_equity(BALANCE)
        self.st = st

    def _since(self) -> float:
        return self.st["reset_at"]

    def equity(self) -> float:
        return float(self.st["cash"])

    def _open(self, coin: str) -> dict | None:
        return next((t for t in self.store.trades("open") if t["coin"] == coin), None)

    def tick(self, now: float | None = None) -> list[str]:
        """Process 4h bars that completed since the last call (cheap to call every few minutes)."""
        now = now or time.time()
        msgs = []
        for coin in COINS:
            c = [x for x in self.mkt.candles(coin, "4h", BARS) if x["t"] + H4 <= now * 1000]
            if len(c) < SLOW + 3:
                continue
            last = self.st["last_t"].get(coin)
            if last is None:                      # first run: start from the latest bar, never trade old history
                self.st["last_t"][coin] = c[-1]["t"]
                continue
            closes = [x["c"] for x in c]
            ef, es = ema(closes, FAST), ema(closes, SLOW)
            for i in range(SLOW + 2, len(c)):
                if c[i]["t"] <= last:
                    continue
                msgs += self._apply(coin, c, i, ef, es)
                self.st["last_t"][coin] = c[i]["t"]
        self.store.set("trend_state", self.st)
        return msgs

    def _apply(self, coin, c, i, ef, es) -> list[str]:
        t = self._open(coin)
        pos = {"side": t["side"], "sl": t["sl"]} if t else None
        a = step(pos, c, i, ef, es)
        bar_close = (c[i]["t"] + H4) / 1000
        if not a:
            return []
        if a[0] == "exit":
            px, kind = a[1], a[2]
            held_h = (bar_close - t["filled_at"]) / 3600
            s = 1 if t["side"] == "long" else -1
            fees = t["qty"] * (t["entry"] + px) * CE / 100 + t["qty"] * t["entry"] * FUND_8H / 100 * held_h / 8
            pnl = s * (px - t["entry"]) * t["qty"] - fees
            r = pnl / t["risk_usd"]
            self.store.update_trade(t["id"], status="closed", exit_px=px, pnl=pnl, result_r=r, exit_type=kind,
                                    closed_at=bar_close, fees=fees)
            self.st["cash"] = self.equity() + pnl
            self.store.record_equity(self.st["cash"])
            return [f"#{t['id']} {coin} {t['side']} closed ({'stop' if kind == 'sl' else 'EMA cross back'}) at "
                    f"{px:,.4g}: {r:+.2f}R ${pnl:+.2f} | equity ${self.equity():.2f}"]
        _, side, entry, stop = a
        eq = self.equity()
        per_unit = abs(entry - stop) + 2 * entry * CE / 100
        qty = min(eq * RISK_PCT / 100 / per_unit, eq * MAX_NOTIONAL_X / entry)
        risk_usd = qty * per_unit
        tid = self.store.add_trade(coin=coin, side=side, status="open", entry=entry, fill_px=entry, sl=stop, tp=None,
                                   qty=qty, leverage=MAX_NOTIONAL_X, created_at=bar_close, filled_at=bar_close,
                                   version="trend-ema-10/200", strategy="trend 4h EMA", risk_usd=risk_usd,
                                   risk_pct=risk_usd / eq * 100)
        return [f"#{tid} {coin} {side.upper()} at {entry:,.4g} (4h EMA10 crossed EMA200) | stop {stop:,.4g} "
                f"({abs(entry - stop) / entry * 100:.1f}%) | exit on the cross back | risk ${risk_usd:.2f}"]

    def status(self) -> str:
        rs = [t["result_r"] for t in self.store.closed(self._since()) if t.get("result_r") is not None]
        c = scorecard(rs)
        lines = ["HEVI TREND (paper, own $100 ledger) - 4h EMA10/200 on BTC, ETH, SOL, 2xATR stop, 1% risk",
                 f"Equity ${self.equity():.2f} (start ${self.st['start']:.2f})"]
        if c["n"]:
            lines.append(f"{c['n']} closed | win {c['win'] * 100:.0f}% | {c['exp']:+.2f}R per trade "
                         f"[{c['lo']:+.2f}, {c['hi']:+.2f}] | total {c['total']:+.1f}R")
        else:
            lines.append("No closed trades yet. Expect about 3 a month across the three coins.")
        for t in self.store.trades("open"):
            lines.append(f"#{t['id']} open {t['coin']} {t['side']} @ {t['entry']:,.4g} stop {t['sl']:,.4g}")
        lines.append("Backtest (2.3 yrs): +0.36R/trade over 90 trades, but only ~+0.04R on the most recent third. "
                     "Real money only if the forward record holds up over 30+ trades.")
        return "\n".join(lines)

    def backtest_report(self) -> str:
        out = ["Trend backtest - Hyperliquid 4h, up to 5000 bars per coin, same rules and costs as the live arm"]
        allr = []
        for coin in COINS:
            end = int(time.time() * 1000)
            c = self.mkt.candles_range(coin, "4h", end - 5000 * H4, end)[:-1]
            tr = backtest(c)
            allr += [r for _, r in tr]
            s = scorecard([r for _, r in tr])
            since = time.strftime("%b %Y", time.gmtime(c[0]["t"] / 1000)) if c else "?"
            out.append(f"- {coin} (since {since}): " + (f"{s['n']} trades | win {s['win'] * 100:.0f}% | {s['exp']:+.2f}R "
                       f"[{s['lo']:+.2f}, {s['hi']:+.2f}] | total {s['total']:+.1f}R" if s["n"] else "no trades"))
        s = scorecard(allr)
        if s["n"]:
            out.append(f"All: {s['n']} trades | {s['exp']:+.2f}R [{s['lo']:+.2f}, {s['hi']:+.2f}] | total {s['total']:+.1f}R")
        return "\n".join(out)
