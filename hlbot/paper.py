"""Paper trading: same interface as Trader, filled against live Hyperliquid prices. No orders reach the exchange.

How fills are simulated (deliberately on the conservative side, so paper results don't flatter the strategy):
- Entries are post-only (ALO), like the live bot: an entry that would cross the book is rejected, as
  Hyperliquid does. A resting entry fills when a 1-minute candle that started after the order was placed
  trades at or through it (maker fee).
- SL and TP are market triggers: they fill at the trigger price minus slippage (taker fee).
- If one candle touches both SL and TP, the SL is assumed to have hit first.
- In the minute the entry fills, only the SL is checked (we can't tell the order of moves inside a candle).
- Funding accrues hourly on open positions at the live rate. Loss is capped at the isolated margin.
- With no SL attached (e.g. after /cancel), a position is liquidated at Hyperliquid's estimated liquidation price
  and the whole margin is booked as lost.

State is stored in the paper database, so a restart resumes where it left off.
"""

from __future__ import annotations

import copy
import logging
import threading
import time

from .config import Config
from .market import Market
from .protocol import PROTOCOL
from .rules.sizing import Fees, liq_price, maintenance
from .store import Store

log = logging.getLogger(__name__)

MIN_MS = 60_000
STATE_KEY = "paper_state"
MAX_FILLS = 500
MAX_REPLAY_MS = 3 * 86_400_000  # Hyperliquid serves at most 5000 1m candles; 3 days is well inside that


class PaperTrader:
    is_paper = True

    def __init__(self, cfg: Config, mkt: Market, store: Store):
        self.cfg = cfg
        self.mkt = mkt
        self.store = store
        f = PROTOCOL.fees
        self.maker, self.taker, self.slip = f.maker_pct / 100, f.taker_pct / 100, f.slippage_pct / 100
        self._lock = threading.RLock()
        self._last_step = 0.0
        self.state = store.get(STATE_KEY) or self._fresh(cfg.paper_balance)
        self._save()

    # ------------------------------------------------------------------ state
    @staticmethod
    def _fresh(balance: float) -> dict:
        return {"cash": float(balance), "start": float(balance), "reset_at": time.time(), "next_oid": 1,
                "orders": [], "positions": {}, "fills": [], "statuses": {}, "cursor": {}}

    def _save(self) -> None:
        self.store.set(STATE_KEY, self.state)

    def _oid(self) -> int:
        oid = self.state["next_oid"]
        self.state["next_oid"] += 1
        return oid

    def reset(self, balance: float) -> None:
        with self._lock:
            self.state = self._fresh(balance)
            self._save()

    @property
    def start_balance(self) -> float:
        return self.state["start"]

    @property
    def reset_at(self) -> float:
        return self.state.get("reset_at", 0.0)

    # ------------------------------------------------------------------ helpers
    def _mark(self, coin: str) -> float:
        return float(self.mkt.ctxs()[coin]["mark"])

    def _exit_px(self, side: str, px: float) -> float:
        """Market exit with slippage against us."""
        return px * (1 - self.slip) if side == "long" else px * (1 + self.slip)

    def _fill(self, coin: str, direction: str, px: float, sz: float, fee: float, closed_pnl: float, t_ms: int,
              funding: float = 0.0, liquidation: bool = False) -> None:
        self.state["fills"].append({"coin": coin, "dir": direction, "px": str(px), "sz": str(sz), "fee": str(fee),
                                    "closedPnl": str(closed_pnl), "time": int(t_ms), "funding": funding,
                                    "liquidation": liquidation})
        del self.state["fills"][:-MAX_FILLS]

    def _open(self, o: dict, px: float, fee_rate: float, t_ms: int) -> None:
        notional = px * o["qty"]
        fee = notional * fee_rate
        margin = notional / o["leverage"]
        self.state["cash"] -= margin + fee
        self.state["positions"][o["coin"]] = {
            "coin": o["coin"], "side": o["side"], "size": o["qty"], "entry": px, "leverage": o["leverage"],
            "margin": margin, "sl": o["sl"], "tp": o["tp"], "sl_oid": self._oid(), "tp_oid": self._oid(),
            "liq": self._liq(o["coin"], o["side"], px, o["qty"], margin),
            "opened_ms": int(t_ms), "funding": 0.0, "funding_ms": int(time.time() * 1000),
        }
        self.state["statuses"][str(o["oid"])] = "filled"
        self._fill(o["coin"], f"Open {o['side'].capitalize()}", px, o["qty"], fee, 0.0, t_ms)

    def _liq(self, coin: str, side: str, entry: float, qty: float, margin: float) -> float:
        tiers = self.mkt.ctxs().get(coin, {}).get("margin_tiers") or [(0.0, 50)]
        mm, _, mmr = maintenance(entry * qty, tiers)
        return liq_price(entry, qty, margin, mm, side, mmr)

    def _close(self, coin: str, px: float, t_ms: int, reason: str) -> None:
        """closedPnl is the price PnL (like Hyperliquid's fills); funding is reported separately."""
        p = self.state["positions"].pop(coin)
        sgn = 1 if p["side"] == "long" else -1
        total = sgn * (px - p["entry"]) * p["size"] - p["funding"]
        fee = px * p["size"] * self.taker
        if reason == "liquidated" or total < -p["margin"]:  # isolated: the margin is the most you can lose,
            total, fee = -p["margin"], 0.0                  # and a liquidation is booked as losing all of it
        self.state["cash"] += p["margin"] + total - fee
        self._fill(coin, f"Close {p['side'].capitalize()}", px, p["size"], fee, total + p["funding"], t_ms,
                   funding=p["funding"], liquidation=reason == "liquidated")
        log.info("paper %s %s closed (%s) at %s pnl %.2f", coin, p["side"], reason, px, total)

    # ------------------------------------------------------------------ simulation
    def _process(self, coin: str, c: dict) -> None:
        """Apply one candle (or a zero-width 'mark' candle) to this coin's orders and position."""
        t, hi, lo = c["t"], c["h"], c["l"]
        for o in [o for o in self.state["orders"] if o["coin"] == coin]:
            if t < o["created_ms"] or coin in self.state["positions"]:
                continue  # candle began before the order existed: its range may predate the order
            if (o["side"] == "long" and lo <= o["entry"]) or (o["side"] == "short" and hi >= o["entry"]):
                self.state["orders"].remove(o)
                self._open(o, o["entry"], self.maker, max(t, o["created_ms"]))

        p = self.state["positions"].get(coin)
        if not p or t + c.get("span", MIN_MS) <= p["opened_ms"]:
            return
        same_candle = t <= p["opened_ms"]
        long = p["side"] == "long"
        hit_sl = p["sl"] is not None and (lo <= p["sl"] if long else hi >= p["sl"])
        hit_liq = p["sl"] is None and (lo <= p["liq"] if long else hi >= p["liq"])
        hit_tp = not same_candle and p["tp"] is not None and (hi >= p["tp"] if long else lo <= p["tp"])
        when = max(t, p["opened_ms"])
        if hit_sl:
            self._close(coin, self._exit_px(p["side"], p["sl"]), when, "sl")
        elif hit_liq:
            self._close(coin, p["liq"], when, "liquidated")
        elif hit_tp:
            self._close(coin, self._exit_px(p["side"], p["tp"]), when, "tp")

    def _accrue_funding(self, coin: str, now_ms: int) -> None:
        p = self.state["positions"].get(coin)
        if not p:
            return
        ctx = self.mkt.ctxs().get(coin, {})
        hours = (now_ms - p["funding_ms"]) / 3_600_000
        sgn = 1 if p["side"] == "long" else -1  # positive funding: longs pay shorts
        p["funding"] += sgn * ctx.get("funding_1h_pct", 0.0) / 100 * p["size"] * ctx.get("mark", p["entry"]) * hours
        p["funding_ms"] = now_ms

    def step(self, force: bool = False) -> None:
        """Replay 1m candles since the last check, then the live mark, for every coin with an order or position."""
        with self._lock:
            if not force and time.time() - self._last_step < 10:
                return
            self._last_step = time.time()
            now_ms = int(time.time() * 1000)
            coins = {o["coin"] for o in self.state["orders"]} | set(self.state["positions"])
            for coin in coins:
                starts = [o["created_ms"] for o in self.state["orders"] if o["coin"] == coin]
                if coin in self.state["positions"]:
                    starts.append(self.state["positions"][coin]["opened_ms"])
                cursor = self.state["cursor"].get(coin) or (min(starts) // MIN_MS * MIN_MS)
                cursor = max(cursor, now_ms - MAX_REPLAY_MS)
                try:
                    candles = sorted(self.mkt.candles_range(coin, "1m", cursor, now_ms), key=lambda c: c["t"])
                except Exception as e:  # noqa: BLE001
                    log.warning("paper candles %s: %s", coin, e)
                    candles = []
                self._accrue_funding(coin, now_ms)  # charge funding up to now before a close can happen
                for c in candles:
                    if c["t"] >= cursor:
                        self._process(coin, c)
                if candles:
                    # the newest candle may still be forming: replay it next time to catch its final range
                    self.state["cursor"][coin] = candles[-1]["t"]
                mark = self._mark(coin)
                self._process(coin, {"t": now_ms, "h": mark, "l": mark, "span": 0})
            active = {o["coin"] for o in self.state["orders"]} | set(self.state["positions"])
            self.state["cursor"] = {k: v for k, v in self.state["cursor"].items() if k in active}
            self._save()

    # ------------------------------------------------------------------ Trader interface
    def place_bracket(self, coin: str, side: str, qty: float, entry: float, sl: float, tp: float, leverage: int) -> dict:
        with self._lock:
            self.step(force=True)
            if coin in self.state["positions"] or any(o["coin"] == coin for o in self.state["orders"]):
                raise RuntimeError(f"paper: {coin} already has a position or resting order")
            mark = self._mark(coin)
            if (side == "long" and mark <= entry) or (side == "short" and mark >= entry):
                raise RuntimeError("Post only order would have immediately matched (paper ALO)")
            need = entry * qty / leverage + entry * qty * self.maker
            if need > self.state["cash"] + 1e-9:
                raise RuntimeError(f"paper: insufficient margin (need ${need:.2f}, available ${self.state['cash']:.2f})")
            o = {"oid": self._oid(), "coin": coin, "side": side, "qty": qty, "entry": entry, "sl": sl, "tp": tp,
                 "leverage": int(leverage), "created_ms": int(time.time() * 1000)}
            self.state["orders"].append(o)
            self.state["statuses"][str(o["oid"])] = "open"
            self._save()
            return {"entry_oid": o["oid"], "filled_now": False, "statuses": []}

    def protect_position(self, coin: str, side: str, size: float, sl: float, tp: float) -> None:
        with self._lock:
            p = self.state["positions"].get(coin)
            if not p:
                raise RuntimeError(f"paper: no open {coin} position")
            p["sl"], p["tp"] = sl, tp
            p["sl_oid"], p["tp_oid"] = self._oid(), self._oid()
            self._save()

    def add_margin(self, coin: str, usd: float) -> None:
        with self._lock:
            p = self.state["positions"].get(coin)
            if not p or usd > self.state["cash"]:
                raise RuntimeError("paper: cannot add margin")
            p["margin"] += usd
            self.state["cash"] -= usd
            p["liq"] = self._liq(coin, p["side"], p["entry"], p["size"], p["margin"])
            self._save()

    def fees(self) -> Fees:
        f = PROTOCOL.fees
        return Fees(f.maker_pct, f.taker_pct, f.slippage_pct, "base tier (paper)", f.slippage_on_tp)

    def cancel(self, coin: str, oid: int) -> None:
        with self._lock:
            for o in list(self.state["orders"]):
                if o["oid"] == oid:
                    self.state["orders"].remove(o)
                    self.state["statuses"][str(oid)] = "canceled"
            p = self.state["positions"].get(coin)
            if p and p.get("sl_oid") == oid:
                p["sl"] = None
            if p and p.get("tp_oid") == oid:
                p["tp"] = None
            self._save()

    def cancel_all(self, coin: str | None = None) -> int:
        with self._lock:
            n = 0
            for o in self.open_orders():
                if coin and o["coin"] != coin:
                    continue
                self.cancel(o["coin"], o["oid"])
                n += 1
            return n

    def close(self, coin: str) -> dict:
        with self._lock:
            self.step(force=True)
            p = self.state["positions"].get(coin)
            if not p:
                raise RuntimeError(f"no open {coin} position")
            self._close(coin, self._exit_px(p["side"], self._mark(coin)), int(time.time() * 1000), "market")
            self.cancel_all(coin)
            self._save()
            return {"status": "ok"}

    def open_orders(self) -> list[dict]:
        with self._lock:
            out = [{"coin": o["coin"], "oid": o["oid"], "isTrigger": False, "orderType": "Limit",
                    "side": "B" if o["side"] == "long" else "A", "limitPx": str(o["entry"]), "sz": str(o["qty"])}
                   for o in self.state["orders"]]
            for p in self.state["positions"].values():
                if p["sl"] is not None:
                    out.append({"coin": p["coin"], "oid": p["sl_oid"], "isTrigger": True, "orderType": "Stop Market",
                                "triggerPx": str(p["sl"])})
                if p["tp"] is not None:
                    out.append({"coin": p["coin"], "oid": p["tp_oid"], "isTrigger": True,
                                "orderType": "Take Profit Market", "triggerPx": str(p["tp"])})
            return out

    def order_status(self, oid: int) -> dict:
        with self._lock:
            st = self.state["statuses"].get(str(oid))
            return {"status": "order", "order": {"status": st}} if st else {"status": "unknownOid"}

    def fills_since(self, since_s: float) -> list[dict]:
        with self._lock:
            return [f for f in self.state["fills"] if f["time"] >= since_s * 1000]

    def funding_paid(self, coin: str, since_s: float) -> float:
        with self._lock:
            done = sum(float(f.get("funding", 0)) for f in self.state["fills"] if f["coin"] == coin and f["time"] >= since_s * 1000)
            live = self.state["positions"].get(coin, {}).get("funding", 0.0)
            return done + live

    def account(self) -> dict:
        with self._lock:
            self.step()
            positions, locked = [], 0.0
            for p in self.state["positions"].values():
                mark = self._mark(p["coin"])
                sgn = 1 if p["side"] == "long" else -1
                upnl = max(sgn * (mark - p["entry"]) * p["size"] - p["funding"], -p["margin"])
                locked += p["margin"] + upnl
                positions.append({"coin": p["coin"], "side": p["side"], "size": p["size"], "entry": p["entry"],
                                  "upnl": upnl, "liq": p["liq"], "leverage": p["leverage"],
                                  "margin_used": p["margin"]})
            return {"account_value": self.state["cash"] + locked, "withdrawable": self.state["cash"],
                    "positions": positions, "spot_usdc": 0.0}

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.state)
