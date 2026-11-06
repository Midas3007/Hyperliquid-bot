"""Module 1 - BTC gate. Macro overrides the screener, always."""

from __future__ import annotations

from ..protocol import Gate

# Strictness order, used by the cancel rule: moving to a stricter band while an entry is unfilled cancels it.
# "pump" trades at normal risk (v2 removed the v1 size-up), so it ranks with "normal".
STRICTNESS = {"pump": 0, "normal": 0, "half": 1, "no_trade": 2}
LABEL = {
    "no_trade": "NO TRADE (BTC -4% or worse)",
    "half": "HALF RISK (max 1 trade, no re-entries)",
    "normal": "NORMAL",
    "pump": "NORMAL + post-pump check (no size-up)",
}


def btc_band(x: float | None, g: Gate) -> str | None:
    """x = BTC % change from the 00:00 UTC open. None = missing data (the gate then fails)."""
    if x is None:
        return None
    if x <= g.no_trade_at_or_below_pct:
        return "no_trade"
    if x <= g.half_risk_at_or_below_pct:
        return "half"
    if x < g.pump_at_or_above_pct:
        return "normal"
    return "pump"


def is_stricter(new: str, old: str) -> bool:
    return STRICTNESS[new] > STRICTNESS[old]


def normal_or_better(band: str | None) -> bool:
    return band in ("normal", "pump")


def post_pump_flags(
    g: Gate,
    *,
    funding_8h_pct: float | None,
    funding_rising: bool | None,
    oi_change_24h_pct: float | None,
    last_4h_move_pct: float | None,
    rejection_at_resistance: bool | None,
    whale_ls_now: float | None,
    whale_ls_24h_ago: float | None,
) -> tuple[list[str], list[str]]:
    """Reversal-risk flags after a BTC pump. Returns (flags raised, inputs that were N/A).
    N/A inputs can't raise a flag; they are listed so the card shows what couldn't be checked."""
    flags, na = [], []
    if funding_8h_pct is None or funding_rising is None:
        na.append("funding trend")
    elif funding_8h_pct >= g.pump_funding_8h_pct and funding_rising:
        flags.append(f"funding {funding_8h_pct:.4f}%/8h and rising")
    if oi_change_24h_pct is None or last_4h_move_pct is None:
        na.append("OI vs price")
    elif oi_change_24h_pct >= g.pump_oi_rising_pct and abs(last_4h_move_pct) < g.pump_price_stall_pct:
        flags.append(f"OI {oi_change_24h_pct:+.1f}% while price stalls ({last_4h_move_pct:+.2f}% last 4H)")
    if rejection_at_resistance is None:
        na.append("4H/1D rejection")
    elif rejection_at_resistance:
        flags.append("rejection wick at 4H/1D resistance")
    if whale_ls_now is None or whale_ls_24h_ago is None or whale_ls_24h_ago <= 0:
        na.append("long-ratio trend")
    elif (whale_ls_now / whale_ls_24h_ago - 1) * 100 >= g.pump_long_ratio_rise_pct:
        flags.append(f"long ratio climbing ({whale_ls_24h_ago:.2f} -> {whale_ls_now:.2f})")
    return flags, na


def reversal_risk(flags: list[str], g: Gate) -> bool:
    return len(flags) >= g.pump_flags_needed
