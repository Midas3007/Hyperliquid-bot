"""Loads protocol.toml (every rules-engine threshold) and calendar.toml (scheduled events).

Strict on purpose: an unknown key, a missing key or a wrong type raises at startup, so a typo in the
config can never silently fall back to a different number.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, get_type_hints

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "protocol.toml")
CALENDAR_PATH = os.path.join(ROOT, "calendar.toml")


@dataclass(frozen=True)
class Data:
    stale_after_min: float
    funding_basis_h: float
    funding_neutral_8h_pct: float


@dataclass(frozen=True)
class Fees:
    maker_pct: float
    taker_pct: float
    slippage_pct: float
    slippage_on_tp: bool


@dataclass(frozen=True)
class Exchange:
    min_order_usd: float
    max_leverage: int


@dataclass(frozen=True)
class Gate:
    no_trade_at_or_below_pct: float
    half_risk_at_or_below_pct: float
    pump_at_or_above_pct: float
    pump_flags_needed: int
    pump_funding_8h_pct: float
    pump_oi_rising_pct: float
    pump_price_stall_pct: float
    pump_long_ratio_rise_pct: float
    wick_min_share: float
    wick_level_tol_pct: float


@dataclass(frozen=True)
class Screener:
    sweep1_min_volume_usd: float
    sweep2_min_volume_usd: float
    sweep1_top_n: int
    sweep2_top_n: int
    sweep3_top_n: int
    max_day_drop_pct: float
    stablecoins: tuple
    unlock_check: bool
    unlock_min_supply_pct: float
    unlock_extra_h: float
    whale_autoskip_below: float
    cooldown_after_sl_h: float
    cooldown_repeat_window_d: float
    cooldown_repeat_count: int
    cooldown_repeat_h: float
    flow_window_min: float
    flow_min_coverage_min: float


@dataclass(frozen=True)
class DeepDive:
    adr_days: int
    atr_period: int
    news_articles: int
    whale_accounts: int
    whale_min_account_usd: float
    whale_min_turnover: float
    whale_max_turnover: float
    whale_list_refresh_h: float
    whale_min_holders: int
    big_trade_window_min: float
    big_trade_min_usd: float
    big_trade_vol_pct: float
    big_trade_min_orders: int
    rv_window_h: int
    rv_lookback_d: int
    vol_low_below_pct: float
    vol_high_at_or_above_pct: float
    vol_extreme_at_or_above_pct: float


@dataclass(frozen=True)
class Filters:
    whale_min_ratio: float
    crowd_max_ratio: float
    oi_flat_tolerance_pct: float
    news_min_bullish: int
    min_net_rr: float


@dataclass(frozen=True)
class Ranking:
    whale_ratio: float
    net_rr: float
    tp_adr_mult: float


@dataclass(frozen=True)
class Tp:
    front_run_min_pct: float
    front_run_atr_mult: float
    max_adr_mult: float
    structural_methods: tuple
    level_merge_pct: float


@dataclass(frozen=True)
class Direction:
    long_whale_min: float
    long_funding_min_8h_pct: float
    long_funding_max_8h_pct: float
    long_fng_min: float
    short_whale_max: float
    short_funding_max_8h_pct: float
    short_fng_max: float
    reversal_needed: int
    reversal_funding_8h_pct: float
    reversal_oi_drop_pct: float
    reversal_spike_pct: float
    reversal_pump_pct: float
    reversal_btc_fast_move_pct: float


@dataclass(frozen=True)
class Entry:
    confluence_methods: tuple
    min_methods: int
    confluence_tol_pct: float
    max_distance_pct: float
    cancel_after_h: float
    cancel_progress_to_tp: float


@dataclass(frozen=True)
class Risk:
    normal_pct: float
    half_pct: float
    ceiling_pct: float
    sl_min_pct: float
    sl_atr_buffer_mult: float
    leverage_buffer_pct: float
    max_margin_of_available_pct: float
    liq_beyond_sl_pct: float
    loss_check_tolerance_pct: float
    funding_hold_h: float
    micro_equity_below_usd: float


@dataclass(frozen=True)
class Breakers:
    half_risk_after_sl_streak: int
    pause_after_sl_streak: int
    pause_h: float
    dd_half_risk_pct: float
    dd_half_risk_until_within_pct: float
    dd_stop_pct: float
    dd_stop_days: float
    max_entries_per_day: int
    half_band_max_entries: int


@dataclass(frozen=True)
class Schedule:
    entry_weekdays: tuple
    friday_cancel_hour: int
    friday_flat_hour: int
    time_stop_h: float
    early_exit_btc_pct: float
    macro_events: tuple


@dataclass(frozen=True)
class Execution:
    reissue_if_moved_pct: float
    card_ttl_min: float


@dataclass(frozen=True)
class Review:
    every_n_trades: int
    recalibrate_lookback: int
    kelly_fraction: float
    no_edge_below_pct: float
    hypothesis_min_trades: int
    skip_check_after_h: float
    discretion_compare_after: int


@dataclass(frozen=True)
class Commands:
    max_resweeps: int


@dataclass(frozen=True)
class Autopilot:
    enabled: bool
    scan_every_min: float
    warmup_min: float
    report_hour_utc: int
    act_on_early_exits: bool
    go_live_min_trades: int
    go_live_min_expectancy_r: float
    go_live_min_profit_factor: float


@dataclass(frozen=True)
class Mode:
    hold_h: float
    max_adr_mult: float
    na_is_miss: bool
    min_measurable: int
    min_measurable_short: int
    long_needed: int
    trend_short_needed: int
    long_buy_sell_min: float
    short_sell_buy_min: float
    news_optional_below: int


@dataclass(frozen=True)
class Strategy:
    default: str
    multi_day_btc_abs_move_pct: float
    multi_day_vol: tuple
    swing: Mode
    day: Mode


@dataclass(frozen=True)
class Demo:
    """v3.3: a one-day demo session on a separate paper account (owner's request). Never touches the real record."""
    entries_until: str      # ISO UTC; no demo entries after this, and the session is off after Friday's flatten
    min_net_rr: float
    max_adr_mult: float
    entry_min_methods: int
    max_entries: int
    scan_every_min: float


@dataclass(frozen=True)
class Scalp:
    version: str
    coin: str
    enabled: bool
    paper_balance: float
    risk_pct: float
    risk_cap_pct: float
    max_leverage: int
    margin_headroom: float
    liq_buffer_mult: float
    stop_min_pct: float
    stop_max_pct: float
    stop_atr_mult: float
    tp1_r: float
    tp2_r: float
    min_net_rr: float
    time_stop_min: float
    time_stop_r: float
    max_hold_min: float
    entry_timeout_min: float
    maker_pct: float
    taker_pct: float
    slippage_pct: float
    sessions_utc: tuple
    macro_buffer_min: float
    atr_min_pct: float
    atr_max_pct: float
    trend_slope_pct: float
    max_trades_day: int
    daily_stop_r: float
    daily_lock_r: float
    loss_streak_cooldown: int
    cooldown_min: float
    max_losses_day: int
    dd_half_pct: float
    dd_pause_pct: float
    gate_min_trades: int
    gate_min_expectancy_r: float
    gate_min_profit_factor: float
    signal_tf: int
    claude_veto: bool


@dataclass(frozen=True)
class Protocol:
    version: str
    data: Data
    fees: Fees
    exchange: Exchange
    gate: Gate
    screener: Screener
    deep_dive: DeepDive
    filters: Filters
    ranking: Ranking
    tp: Tp
    direction: Direction
    entry: Entry
    risk: Risk
    breakers: Breakers
    schedule: Schedule
    execution: Execution
    review: Review
    commands: Commands
    autopilot: Autopilot
    strategy: Strategy
    demo: Demo
    scalp: Scalp


def _coerce(name: str, typ: Any, v: Any) -> Any:
    if typ is bool and isinstance(v, bool):
        return v
    if typ is float and isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if typ is int and isinstance(v, int) and not isinstance(v, bool):
        return v
    if typ is str and isinstance(v, str):
        return v
    if typ is tuple and isinstance(v, list):
        return tuple(v)
    raise TypeError(f"protocol.toml: {name} should be {typ.__name__}, got {v!r}")


def _build(cls: type, table: dict, path: str) -> Any:
    hints = get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(table) - names
    missing = names - set(table)
    if unknown or missing:
        raise KeyError(f"protocol.toml [{path}]: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
    kw = {}
    for n in names:
        t = hints[n]
        if dataclasses.is_dataclass(t):
            if not isinstance(table[n], dict):
                raise TypeError(f"protocol.toml: [{n}] must be a table")
            kw[n] = _build(t, table[n], n)
        else:
            kw[n] = _coerce(f"{path}.{n}" if path else n, t, table[n])
    return cls(**kw)


def load(path: str = DEFAULT_PATH) -> Protocol:
    with open(path, "rb") as f:
        return _build(Protocol, tomllib.load(f), "")


# ------------------------------------------------------------------ calendar
@dataclass(frozen=True)
class Event:
    name: str  # CPI | FOMC | NFP | UNLOCK
    ts: float
    coin: str | None = None
    supply_pct: float | None = None


@dataclass(frozen=True)
class Calendar:
    macro: tuple[Event, ...]
    macro_covered_until: float | None
    unlocks: tuple[Event, ...]
    unlocks_covered_until: float | None
    loaded_at: float


def _ts(s: str) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def load_calendar(path: str = CALENDAR_PATH) -> Calendar:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    m, u = raw.get("macro", {}), raw.get("unlocks", {})
    macro = tuple(Event(e["name"].upper(), _ts(e["time"])) for e in m.get("events", []))
    unlocks = tuple(Event("UNLOCK", _ts(e["time"]), e["coin"].upper(), float(e["supply_pct"])) for e in u.get("events", []))
    return Calendar(macro, _ts(m.get("covered_until", "")), unlocks, _ts(u.get("covered_until", "")),
                    os.path.getmtime(path))


PROTOCOL = load()
