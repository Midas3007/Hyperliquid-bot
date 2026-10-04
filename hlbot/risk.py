"""Module 6 (risk parameters) and Module 7 (position sizing). Pure functions, unit-tested.

Two sizing modes:
  A  "protocol": margin = 80-85% of balance, leverage = floor(1/(SL+0.5%)) capped 25x, floor 15x.
  B  "capped":   leverage chosen so a stop-out loses a fixed % of the account (default 15%).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class SizingInput:
    side: str  # long | short
    entry: float
    sl: float
    tp: float
    balance: float  # account value used for sizing
    available: float  # margin actually available on the exchange
    coin_max_leverage: int
    sz_decimals: int
    mode: str  # A | B
    mode_a_margin_pct: float = 80.0
    mode_b_risk_pct: float = 15.0
    mode_b_max_margin_pct: float = 85.0
    custom_margin: float | None = None  # user override ("margin $5")
    margin_cap: float | None = None  # BTC gate "reduced" state
    max_leverage: int = 25
    min_leverage_mode_a: int = 15
    min_sl_pct: float = 1.5
    min_rr: float = 3.0
    maker_fee: float = 0.00015
    taker_fee: float = 0.00045
    min_order_usd: float = 10.0


@dataclass
class Sizing:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    leverage: int = 0
    margin: float = 0.0
    notional: float = 0.0
    qty: float = 0.0
    liq_protocol: float = 0.0
    liq_exchange_est: float = 0.0
    loss_at_sl: float = 0.0
    gain_at_tp: float = 0.0
    loss_pct_of_margin: float = 0.0
    loss_pct_of_balance: float = 0.0
    gain_pct_of_balance: float = 0.0
    fees_round_trip: float = 0.0
    rr: float = 0.0


def protocol_max_leverage(sl_pct: float, cap: int) -> int:
    """`1 / (SL% + 0.005)` rounded down, capped."""
    return max(1, min(cap, math.floor(1 / (sl_pct / 100 + 0.005))))


def liq_protocol(entry: float, lev: int, side: str) -> float:
    """The protocol's liquidation estimate (Bybit-style, 0.5% maintenance)."""
    if side == "long":
        return entry * (1 - 1 / lev + 0.005)
    return entry * (1 + 1 / lev - 0.005)


def liq_hyperliquid(entry: float, lev: int, side: str, coin_max_leverage: int) -> float:
    """Isolated-margin liquidation estimate using Hyperliquid's maintenance margin
    (half the initial margin at the coin's max leverage)."""
    mmr = 1 / (2 * coin_max_leverage)
    s = 1 if side == "long" else -1
    margin_per_unit = entry / lev - mmr * entry
    return entry - s * margin_per_unit / (1 - mmr * s)


def safe_leverage(entry: float, sl: float, side: str, coin_max_leverage: int, cap: int) -> int:
    """Highest leverage whose liquidation (both the protocol formula and Hyperliquid's
    maintenance-margin estimate) stays beyond the stop loss."""
    for lev in range(cap, 0, -1):
        lp = liq_protocol(entry, lev, side)
        lh = liq_hyperliquid(entry, lev, side, coin_max_leverage)
        if side == "long" and max(lp, lh) < sl:
            return lev
        if side == "short" and min(lp, lh) > sl:
            return lev
    return 1


def round_size(qty: float, sz_decimals: int) -> float:
    f = 10**sz_decimals
    return math.floor(qty * f + 1e-9) / f


def round_price(px: float, sz_decimals: int, perp: bool = True) -> float:
    """Hyperliquid tick rules: max 5 significant figures and (6 - szDecimals) decimals for perps."""
    if px <= 0:
        return px
    max_dec = (6 if perp else 8) - sz_decimals
    sig = float(f"{px:.5g}")
    return round(sig, max(0, max_dec))


def size_position(p: SizingInput) -> Sizing:
    out = Sizing(ok=True)
    r = out.reasons
    if p.side not in ("long", "short"):
        return Sizing(ok=False, reasons=[f"bad side {p.side}"])
    s = 1 if p.side == "long" else -1
    sl_pct = s * (p.entry - p.sl) / p.entry * 100
    tp_pct = s * (p.tp - p.entry) / p.entry * 100
    if sl_pct <= 0:
        return Sizing(ok=False, reasons=["SL is on the wrong side of entry"])
    if tp_pct <= 0:
        return Sizing(ok=False, reasons=["TP is on the wrong side of entry"])
    if sl_pct + 1e-9 < p.min_sl_pct:
        r.append(f"SL {sl_pct:.2f}% below {p.min_sl_pct}% hard floor")
    out.rr = tp_pct / sl_pct
    if out.rr + 1e-9 < p.min_rr:
        r.append(f"R/R 1:{out.rr:.2f} below 1:{p.min_rr:g}")

    lev_cap = min(p.max_leverage, p.coin_max_leverage)
    lev_formula = min(
        protocol_max_leverage(sl_pct, p.max_leverage),
        safe_leverage(p.entry, p.sl, p.side, p.coin_max_leverage, p.max_leverage),
    )
    rt_fee = p.maker_fee + p.taker_fee  # limit entry, market stop

    if p.mode == "A":
        lev = min(lev_formula, lev_cap)
        if lev < p.min_leverage_mode_a:
            why = (
                f"coin max leverage {p.coin_max_leverage}x"
                if p.coin_max_leverage < p.min_leverage_mode_a
                else f"SL {sl_pct:.2f}% too wide"
            )
            r.append(f"leverage {lev}x below {p.min_leverage_mode_a}x floor ({why})")
        margin = p.balance * p.mode_a_margin_pct / 100
        if p.custom_margin is not None:
            margin = p.custom_margin
        if p.margin_cap is not None:
            margin = min(margin, p.margin_cap)
        margin = min(margin, p.available)
        notional = margin * lev
    else:
        risk_usd = p.balance * p.mode_b_risk_pct / 100
        max_margin = min(p.balance * p.mode_b_max_margin_pct / 100, p.available)
        if p.custom_margin is not None:
            max_margin = min(p.custom_margin, p.available)
        if p.margin_cap is not None:
            max_margin = min(max_margin, p.margin_cap)
        target_notional = risk_usd / (sl_pct / 100 + rt_fee)
        lev_max = min(lev_formula, lev_cap)
        lev = max(1, min(lev_max, math.ceil(target_notional / max_margin))) if max_margin > 0 else 1
        notional = min(target_notional, max_margin * lev)
        margin = notional / lev

    qty = round_size(notional / p.entry, p.sz_decimals)
    notional = qty * p.entry
    margin = notional / lev if lev else 0.0
    out.leverage, out.margin, out.notional, out.qty = lev, margin, notional, qty

    if notional < p.min_order_usd:
        r.append(f"order ${notional:.2f} below Hyperliquid ${p.min_order_usd:g} minimum")
    if margin > p.available + 1e-9:
        r.append(f"margin ${margin:.2f} exceeds available ${p.available:.2f}")

    out.liq_protocol = liq_protocol(p.entry, lev, p.side)
    out.liq_exchange_est = liq_hyperliquid(p.entry, lev, p.side, p.coin_max_leverage)
    # use whichever liquidation is closer to entry (more conservative)
    liq = max(out.liq_protocol, out.liq_exchange_est) if p.side == "long" else min(out.liq_protocol, out.liq_exchange_est)
    if p.side == "long" and not liq < p.sl:
        r.append(f"liq {liq:.6g} not BELOW SL {p.sl:.6g}")
    if p.side == "short" and not liq > p.sl:
        r.append(f"liq {liq:.6g} not ABOVE SL {p.sl:.6g}")

    out.fees_round_trip = notional * rt_fee
    out.loss_at_sl = abs(p.entry - p.sl) * qty + out.fees_round_trip
    out.gain_at_tp = abs(p.tp - p.entry) * qty - notional * (p.maker_fee + p.taker_fee)
    if margin > 0:
        out.loss_pct_of_margin = out.loss_at_sl / margin * 100
        if out.loss_at_sl >= margin:
            r.append(f"loss at SL ${out.loss_at_sl:.2f} >= 100% of margin")
    if p.balance > 0:
        out.loss_pct_of_balance = out.loss_at_sl / p.balance * 100
        out.gain_pct_of_balance = out.gain_at_tp / p.balance * 100
    out.ok = not r
    return out
