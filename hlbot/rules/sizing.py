"""Module 7 - risk and sizing. Size by dollar risk at the SL (costs included); leverage only sets the margin posted.

  C        = maker + taker + slippage                      (loss-side costs, % of notional)
  notional = equity x risk% / (SL% + C)
  qty      = notional / entry, rounded DOWN to the coin's size step
  leverage = floor(1 / (SL% + MMR + 2%)), capped at 25x and the coin's max for that size. No floor.
  margin   = notional / leverage                           (<= 80% of available)
  liq      = Hyperliquid isolated formula, must sit >= 1.5% beyond the SL
  net R/R  = (TP move - entry fee - exit fee - TP slippage - funding over the hold) / (SL% + C), >= 1:3
  loss at SL (costs incl.) must equal the planned R within 5%

Micro phase (v2.1, rebuilt for Hyperliquid's $10 minimum): below `micro_equity_below_usd`, if the minimum order
forces more than normal risk, risk may rise up to the 15% ceiling - only as far as the minimum needs, never in a
half-risk state. If the minimum needs more than 15%, skip.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..protocol import Exchange, Filters, Risk
from .checks import Check


# ------------------------------------------------------------------ exchange rounding
def floor_step(qty: float, sz_decimals: int) -> float:
    f = 10 ** sz_decimals
    return math.floor(qty * f + 1e-9) / f


def ceil_step(qty: float, sz_decimals: int) -> float:
    f = 10 ** sz_decimals
    return math.ceil(qty * f - 1e-9) / f


def round_price(px: float, sz_decimals: int, perp: bool = True) -> float:
    """Hyperliquid tick rules: at most 5 significant figures and (6 - szDecimals) decimals for perps."""
    if px <= 0:
        return px
    max_dec = (6 if perp else 8) - sz_decimals
    return round(float(f"{px:.5g}"), max(0, max_dec))


def price_decimals(px: float, sz_decimals: int, perp: bool = True) -> int:
    """Decimals allowed for this price: 5 significant figures, at most (6 - szDecimals); integers always allowed."""
    sig = 5 - (math.floor(math.log10(px)) + 1)
    return max(0, min((6 if perp else 8) - sz_decimals, sig))


def round_price_dir(px: float, sz_decimals: int, up: bool) -> float:
    """Round to a valid Hyperliquid price in a chosen direction (e.g. an SL rounds AWAY from entry, so rounding
    can never pull it inside the 1.5% floor)."""
    dec = price_decimals(px, sz_decimals)
    f = 10 ** dec
    v = (math.ceil(px * f - 1e-9) if up else math.floor(px * f + 1e-9)) / f
    return round(v, dec)


# ------------------------------------------------------------------ Hyperliquid margin tables
def maintenance(notional: float, tiers: list[tuple[float, int]]) -> tuple[float, int, float]:
    """(maintenance margin $, max leverage at this size, effective MMR) from a margin table.

    tiers: [(lower_bound_usd, max_leverage)] ascending. Maintenance rate of a tier = 1 / (2 x its max leverage).
    Deductions keep maintenance margin continuous across tier boundaries."""
    tiers = sorted(tiers)
    deduction, prev_rate, cur, cur_deduction = 0.0, None, tiers[0], 0.0
    for lb, lev in tiers:
        rate = 1 / (2 * lev)
        if prev_rate is not None:
            deduction += lb * (rate - prev_rate)
        if notional < lb:
            break
        cur, prev_rate = (lb, lev), rate
        cur_deduction = deduction
    rate = 1 / (2 * cur[1])
    mm = max(0.0, notional * rate - cur_deduction)
    return mm, cur[1], (mm / notional if notional > 0 else rate)


def liq_price(entry: float, qty: float, margin: float, maint: float, side: str, mmr: float) -> float:
    """Hyperliquid isolated liquidation price:
    liq = entry - side * margin_available / size / (1 - mmr * side), margin_available = margin - maintenance."""
    s = 1 if side == "long" else -1
    return entry - s * (margin - maint) / qty / (1 - mmr * s)


def liq_buffer_pct(liq: float, sl: float, entry: float, side: str) -> float:
    """How far the liquidation sits beyond the SL, as % of the larger of entry/SL (the stricter base)."""
    gap = (sl - liq) if side == "long" else (liq - sl)
    return gap / max(entry, sl) * 100


def leverage_for(sl_pct: float, mmr: float, buffer_pct: float, cap: int) -> int:
    return max(1, min(cap, math.floor(1 / (sl_pct / 100 + mmr + buffer_pct / 100) + 1e-9)))


# ------------------------------------------------------------------ sizing
@dataclass(frozen=True)
class Fees:
    maker_pct: float
    taker_pct: float
    slippage_pct: float
    source: str = "default"
    slippage_on_tp: bool = True  # the TP is a market trigger, so its fill slips too

    @property
    def c_pct(self) -> float:
        return self.maker_pct + self.taker_pct + self.slippage_pct


@dataclass(frozen=True)
class SizingInput:
    side: str
    entry: float
    sl: float
    tp: float
    equity: float
    available: float
    risk_pct: float            # risk for this trade before the micro phase (normal 10 / half 5 / user-set)
    half_risk_state: bool      # BTC caution band or a breaker: the micro phase may not raise risk
    sz_decimals: int
    margin_tiers: list         # [(lower_bound_usd, max_leverage)]
    fees: Fees
    funding_8h_pct: float      # current funding on the 8h basis (positive = longs pay)


@dataclass
class Sizing:
    checks: list[Check] = field(default_factory=list)
    risk_mode: str = "normal"   # normal | half | micro
    risk_pct: float = 0.0
    planned_r: float = 0.0
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    qty: float = 0.0
    notional: float = 0.0
    leverage: int = 0
    max_leverage: int = 0
    mmr: float = 0.0
    margin: float = 0.0
    margin_pct_of_equity: float = 0.0
    liq: float = 0.0
    liq_buffer_pct: float = 0.0
    entry_fee: float = 0.0
    exit_fee_tp: float = 0.0
    slippage_tp: float = 0.0
    funding_cost: float = 0.0
    loss_at_sl: float = 0.0
    net_reward: float = 0.0
    net_rr: float = 0.0

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def size(p: SizingInput, r: Risk, x: Exchange, f: Filters) -> Sizing:
    out = Sizing()
    ck = out.checks.append
    s = 1 if p.side == "long" else -1
    out.sl_pct = s * (p.entry - p.sl) / p.entry * 100
    out.tp_pct = s * (p.tp - p.entry) / p.entry * 100
    if out.sl_pct <= 0 or out.tp_pct <= 0:
        ck(Check("M7", "bracket sides", False, "SL or TP on the wrong side of entry", hard=True))
        return out
    ck(Check("M7", "SL >= 1.5%", out.sl_pct + 1e-9 >= r.sl_min_pct, f"SL {out.sl_pct:.2f}% from entry", hard=True))
    if p.equity <= 0:
        ck(Check("M7", "equity", False, "equity is $0", hard=True))
        return out

    c = p.fees.c_pct / 100
    risk_pct = min(p.risk_pct, r.ceiling_pct)
    out.risk_mode = "half" if p.half_risk_state else "normal"
    notional = p.equity * risk_pct / 100 / (out.sl_pct / 100 + c)
    qty = floor_step(notional / p.entry, p.sz_decimals)

    if qty * p.entry < x.min_order_usd:
        # Micro phase: the exchange minimum forces more risk than planned
        q_min = ceil_step(x.min_order_usd / p.entry, p.sz_decimals)
        need_pct = q_min * p.entry * (out.sl_pct / 100 + c) / p.equity * 100
        if p.half_risk_state:
            ck(Check("M7", "exchange minimum", False,
                     f"${qty * p.entry:.2f} order < ${x.min_order_usd:g} minimum; micro phase not allowed in a half-risk state"))
            return out
        if p.equity >= r.micro_equity_below_usd:
            ck(Check("M7", "exchange minimum", False,
                     f"${qty * p.entry:.2f} order < ${x.min_order_usd:g} minimum at {risk_pct:g}% risk (equity "
                     f"${p.equity:.2f} is above the ${r.micro_equity_below_usd:g} micro phase)"))
            return out
        if need_pct > r.ceiling_pct + 1e-9:
            ck(Check("M7", "exchange minimum", False,
                     f"${x.min_order_usd:g} minimum needs {need_pct:.1f}% risk > {r.ceiling_pct:g}% ceiling: skip"))
            return out
        qty, risk_pct, out.risk_mode = q_min, need_pct, "micro"

    out.risk_pct = risk_pct
    out.planned_r = p.equity * risk_pct / 100
    out.qty = qty
    out.notional = qty * p.entry

    mm, tier_max, mmr = maintenance(out.notional, p.margin_tiers)
    out.max_leverage, out.mmr = tier_max, mmr
    out.leverage = leverage_for(out.sl_pct, mmr, r.leverage_buffer_pct, min(x.max_leverage, tier_max))
    out.margin = out.notional / out.leverage
    out.margin_pct_of_equity = out.margin / p.equity * 100
    cap = p.available * r.max_margin_of_available_pct / 100
    ck(Check("M7", "margin <= 80% of available", out.margin <= cap + 1e-9,
             f"margin ${out.margin:.2f} vs cap ${cap:.2f}", hard=True))

    out.liq = liq_price(p.entry, qty, out.margin, mm, p.side, mmr)
    out.liq_buffer_pct = liq_buffer_pct(out.liq, p.sl, p.entry, p.side)
    ck(Check("M7", "liq >= 1.5% beyond SL", out.liq_buffer_pct + 1e-9 >= r.liq_beyond_sl_pct,
             f"liq {out.liq:.6g} is {out.liq_buffer_pct:.2f}% beyond the SL", hard=True))

    out.entry_fee = out.notional * p.fees.maker_pct / 100        # post-only entry pays maker
    out.exit_fee_tp = qty * p.tp * p.fees.taker_pct / 100          # TP trigger fills as a market order
    out.slippage_tp = qty * p.tp * p.fees.slippage_pct / 100 if p.fees.slippage_on_tp else 0.0
    hourly = s * p.funding_8h_pct / 100 / 8                         # >0 = this side pays
    out.funding_cost = max(0.0, hourly * out.notional * r.funding_hold_h)
    out.loss_at_sl = out.notional * (out.sl_pct / 100 + c)
    out.net_reward = qty * abs(p.tp - p.entry) - out.entry_fee - out.exit_fee_tp - out.slippage_tp - out.funding_cost
    out.net_rr = out.net_reward / out.loss_at_sl if out.loss_at_sl > 0 else 0.0
    ck(Check("M7", "net R/R >= 1:3", out.net_rr + 1e-9 >= f.min_net_rr, f"net R/R 1:{out.net_rr:.2f}", hard=True))

    dev = abs(out.loss_at_sl - out.planned_r) / out.planned_r * 100 if out.planned_r else 100
    ck(Check("M7", "loss check", dev <= r.loss_check_tolerance_pct + 1e-9,
             f"loss at SL ${out.loss_at_sl:.2f} vs planned R ${out.planned_r:.2f} ({dev:.1f}% apart)", hard=True))
    ck(Check("M7", "risk <= 15%", out.loss_at_sl / p.equity * 100 <= r.ceiling_pct + 1e-9,
             f"{out.loss_at_sl / p.equity * 100:.1f}% of equity at SL", hard=True))
    return out
