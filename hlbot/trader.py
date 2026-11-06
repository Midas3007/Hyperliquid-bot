"""Order execution on Hyperliquid via an API (agent) wallet - it can trade but cannot withdraw.

Entries are post-only (ALO): Hyperliquid rejects an ALO order that would cross the book instead of filling it
as a taker, so a mistyped price can never fill at market. SL and TP are reduce-only market triggers, which
Hyperliquid fires on the MARK price. With grouping "normalTpsl" the TP/SL become active once the entry fills
completely; a partial fill gets position-level TP/SL from protect_position() on the next sync.
"""

from __future__ import annotations

import logging
import time

import eth_account
from hyperliquid.exchange import Exchange

from .config import Config
from .market import Market
from .rules.sizing import round_price

log = logging.getLogger(__name__)

TRIGGER_SLIPPAGE = 0.10  # worst-price bound on market TP/SL triggers, same as the Hyperliquid UI


def _statuses(resp: dict) -> list[dict]:
    if not isinstance(resp, dict) or resp.get("status") != "ok":
        raise RuntimeError(f"exchange error: {resp}")
    data = resp.get("response", {}).get("data", {})
    return data.get("statuses", [])


class Trader:
    is_paper = False

    def __init__(self, cfg: Config, mkt: Market):
        self.cfg = cfg
        self.mkt = mkt
        self.address = cfg.hl_account_address
        wallet = eth_account.Account.from_key(cfg.hl_api_private_key)
        self.ex = Exchange(wallet, mkt.base_url, account_address=self.address)

    # ------------------------------------------------------------------ helpers
    def _px(self, coin: str, px: float) -> float:
        return round_price(px, self.mkt.ctxs()[coin]["sz_decimals"])

    def _trigger(self, coin: str, is_buy: bool, sz: float, trigger: float, kind: str) -> dict:
        bound = trigger * (1 + TRIGGER_SLIPPAGE) if is_buy else trigger * (1 - TRIGGER_SLIPPAGE)
        return {
            "coin": coin, "is_buy": is_buy, "sz": sz, "limit_px": self._px(coin, bound),
            "order_type": {"trigger": {"triggerPx": self._px(coin, trigger), "isMarket": True, "tpsl": kind}},
            "reduce_only": True,
        }

    # ------------------------------------------------------------------ actions
    def place_bracket(self, coin: str, side: str, qty: float, entry: float, sl: float, tp: float, leverage: int) -> dict:
        """Isolated leverage, then limit entry + SL + TP as one normalTpsl group
        (TP/SL activate when the entry fills)."""
        r = self.ex.update_leverage(int(leverage), coin, is_cross=False)
        if r.get("status") != "ok":
            raise RuntimeError(f"set leverage failed: {r}")
        is_buy = side == "long"
        orders = [
            {"coin": coin, "is_buy": is_buy, "sz": qty, "limit_px": self._px(coin, entry),
             "order_type": {"limit": {"tif": "Alo"}}, "reduce_only": False},
            self._trigger(coin, not is_buy, qty, sl, "sl"),
            self._trigger(coin, not is_buy, qty, tp, "tp"),
        ]
        st = _statuses(self.ex.bulk_orders(orders, grouping="normalTpsl"))
        errors = [s["error"] for s in st if "error" in s]
        if errors:
            raise RuntimeError("; ".join(errors))
        first = st[0]
        oid = (first.get("resting") or first.get("filled") or {}).get("oid")
        return {"entry_oid": oid, "filled_now": "filled" in first, "statuses": st}

    def protect_position(self, coin: str, side: str, size: float, sl: float, tp: float) -> None:
        """Position-level TP/SL (used if a fill isn't covered, e.g. after a partial fill)."""
        is_buy = side != "long"
        orders = [self._trigger(coin, is_buy, size, sl, "sl"), self._trigger(coin, is_buy, size, tp, "tp")]
        st = _statuses(self.ex.bulk_orders(orders, grouping="positionTpsl"))
        errors = [s["error"] for s in st if "error" in s]
        if errors:
            raise RuntimeError("; ".join(errors))

    def add_margin(self, coin: str, usd: float) -> None:
        """Add isolated margin to a position (Module 7: restore the 1.5% liq buffer after a fill)."""
        r = self.ex.update_isolated_margin(round(usd, 2), coin)
        if not isinstance(r, dict) or r.get("status") != "ok":
            raise RuntimeError(f"add margin failed: {r}")

    def cancel(self, coin: str, oid: int) -> None:
        self.ex.cancel(coin, oid)

    def cancel_all(self, coin: str | None = None) -> int:
        n = 0
        for o in self.open_orders():
            if coin and o["coin"] != coin:
                continue
            try:
                self.ex.cancel(o["coin"], o["oid"])
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("cancel %s: %s", o, e)
        return n

    def close(self, coin: str) -> dict:
        r = self.ex.market_close(coin)
        if r is None:
            raise RuntimeError(f"no open {coin} position")
        _statuses(r)
        self.cancel_all(coin)
        return r

    # ------------------------------------------------------------------ queries
    def account(self) -> dict:
        return self.mkt.account(self.address)

    def funding_paid(self, coin: str, since_s: float) -> float:
        """Funding paid on `coin` since `since_s` (positive = paid, negative = received)."""
        rows = self.ex.info.user_funding_history(self.address, int(since_s * 1000), int(time.time() * 1000))
        return -sum(float(r["delta"]["usdc"]) for r in rows
                    if r.get("delta", {}).get("type") == "funding" and r["delta"].get("coin") == coin)

    def fees(self):
        return self.mkt.user_fees(self.address)

    def open_orders(self) -> list[dict]:
        return self.ex.info.frontend_open_orders(self.address)

    def order_status(self, oid: int) -> dict:
        return self.ex.info.query_order_by_oid(self.address, oid)

    def fills_since(self, since_s: float) -> list[dict]:
        return self.ex.info.user_fills_by_time(self.address, int(since_s * 1000), int(time.time() * 1000))
