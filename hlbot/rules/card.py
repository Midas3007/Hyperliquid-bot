"""The confirmation card (v2 layout, Hyperliquid fields) and the Module 13 final check run right before placing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..levels import fmt
from ..protocol import Protocol
from . import schedule
from .sizing import Fees, Sizing


@dataclass
class Plan:
    coin: str
    side: str
    entry: float
    sl: float
    tp: float
    entry_methods: tuple
    sl_label: str
    tp_label: str
    tp_adr_mult: float
    sizing: Sizing
    fees: Fees
    equity: float
    equity_ts: float
    btc_band: str
    vol_label: str
    score: int
    score_points: list
    data_ts: float                   # oldest timestamp among the inputs used
    breakers: str
    notes: list = field(default_factory=list)      # cautions, shown with "!"
    info: list = field(default_factory=list)       # the data behind the card, with sources
    runner_up: str | None = None
    override: str | None = None      # reason, when the user overrode soft rules
    overridden_rules: list = field(default_factory=list)
    price_at_card: float = 0.0
    tag: str = ""


def _t(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M")


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%a %d %b %H:%M")


def render(p: Plan, P: Protocol, now: float, paper: bool, index: int | None = None) -> str:
    z = p.sizing
    sgn = 1 if p.side == "long" else -1
    mode = {"normal": f"Normal {z.risk_pct:g}%", "half": f"Half {z.risk_pct:g}%",
            "micro": f"Micro {z.risk_pct:.1f}% (forced by the ${P.exchange.min_order_usd:g} minimum)"}[z.risk_mode]
    stop = schedule.time_stop_at(now + P.entry.cancel_after_h * 3600, P.schedule)
    tag = f"[{index}] " if index else ""
    head = f"# {tag}HEVI - Open Limit Order - Protocol v{P.version} (Hyperliquid)" + (" - PAPER" if paper else "")
    lines = [
        head,
        "--------------------------",
        f"Symbol:      {p.coin}-PERP (Hyperliquid)",
        f"Direction:   {p.side.capitalize()}",
        "Margin mode: Isolated",
        f"Limit:       ${fmt(p.entry)} post-only (ALO) - 5m confluence {'+'.join(p.entry_methods)}",
        f"Qty:         {z.qty:g} {p.coin} (~${z.notional:,.2f} notional)",
        f"Leverage:    {z.leverage}x (MMR {z.mmr * 100:.2f}%, coin max {z.max_leverage}x)",
        f"Margin used: ~${z.margin:,.2f} ({z.margin_pct_of_equity:.0f}% of equity)",
        f"TP:          ${fmt(p.tp)} ({sgn * (p.tp / p.entry - 1) * 100:+.2f}%) - in front of {p.tp_label}",
        f"SL:          ${fmt(p.sl)} ({-z.sl_pct:+.2f}%) - {p.sl_label}, mark-price trigger",
        f"Liq:         ~${z.liq:.5g} - {z.liq_buffer_pct:.2f}% beyond SL OK",
        "--------------------------",
        f"Equity:      ${p.equity:,.2f} (confirmed {_t(p.equity_ts)} UTC)",
        f"Risk @ SL:   ~${z.loss_at_sl:,.2f} ({z.loss_at_sl / p.equity * 100:.1f}% of equity, costs incl.)",
        f"Net @ TP:    ~${z.net_reward:,.2f} (+{z.net_reward / p.equity * 100:.1f}% of equity)",
        f"Net R/R:     1:{z.net_rr:.2f} OK",
        f"Costs:       fees ~${z.entry_fee + z.exit_fee_tp:.2f} ({p.fees.source}) | TP slippage ~${z.slippage_tp:.2f} | "
        f"funding ~${z.funding_cost:.2f} (48h)",
        f"Time stop:   {_when(stop)} UTC latest (48h after fill / Fri {P.schedule.friday_flat_hour}:00 UTC)",
        f"Risk mode:   {mode} | Breakers: {p.breakers}",
        f"Setup score: {p.score}/6" + (f" ({', '.join(p.score_points)})" if p.score_points else "")
        + (f" | runner-up: {p.runner_up}" if p.runner_up else ""),
        f"BTC band:    {p.btc_band} | volatility {p.vol_label}",
        f"Data as of:  {_t(p.data_ts)} UTC",
    ]
    for n in p.info:
        lines.append(f"  {n}")
    for n in p.notes:
        lines.append(f"! {n}")
    if p.override:
        lines.append(f"OVERRIDE: {', '.join(p.overridden_rules)} - reason: {p.override}")
    lines.append("--------------------------")
    return "\n".join(lines)


def final_check(p: Plan, P: Protocol, *, now: float, open_positions: int, pending_entries: int,
                breakers_blocked: list[str], authorized: bool) -> list[str]:
    """Module 13 hard rules, re-checked on the exact order about to be sent. Overrides never bypass these.
    `authorized`: the user typed 'y', or autopilot is on (v3.1)."""
    z, r = p.sizing, P.risk
    bad = []
    if not authorized:
        bad.append("not authorized: no 'y' from the user and autopilot is off")
    if not (p.sl and p.tp):
        bad.append("TP and SL must be attached at entry")
    if open_positions or pending_entries:
        bad.append("max 1 open position (one is open or pending)")
    if z.loss_at_sl / p.equity * 100 > r.ceiling_pct + 1e-9 or z.risk_pct > r.ceiling_pct + 1e-9:
        bad.append(f"risk above the {r.ceiling_pct:g}% ceiling")
    if z.sl_pct + 1e-9 < r.sl_min_pct or not p.sl_label.startswith("past 4h"):
        bad.append("SL must be >= 1.5% and beyond 4H structure")
    if z.liq_buffer_pct + 1e-9 < r.liq_beyond_sl_pct:
        bad.append("liquidation must be >= 1.5% beyond the SL")
    if z.net_rr + 1e-9 < P.filters.min_net_rr:
        bad.append("net R/R below 1:3")
    if not p.tp_label.startswith("4h") or p.tp_adr_mult > P.tp.max_adr_mult + 1e-9:
        bad.append("TP must sit at a real, reachable 4H level")
    if p.btc_band in (None, "no_trade"):
        bad.append("BTC -4% or worse (or gate data missing): no new trades")
    if p.vol_label in (None, "EXTREME"):
        bad.append("EXTREME (or missing) volatility: skip")
    if not schedule.entry_window_open(now, P.schedule) or schedule.must_be_flat(now, P.schedule):
        bad.append("no entries Friday to Sunday")
    if breakers_blocked:
        bad.append("circuit breaker active: " + "; ".join(breakers_blocked))
    stale = P.data.stale_after_min * 60
    if now - p.data_ts > stale:
        bad.append("card data is older than 15 minutes")
    if now - p.equity_ts > stale:
        bad.append("equity not confirmed in the last 15 minutes")
    if not z.ok:
        bad += [c.line() for c in z.checks if not c.ok]
    return bad
