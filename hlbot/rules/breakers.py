"""Module 8 - circuit breakers. They fire automatically; the bot announces them, it doesn't ask.

  SL hit                     -> no new entries until the next UTC day
  2 consecutive SLs          -> half risk until the next TP
  3 consecutive SLs          -> 48h pause + a full review before trading again
  equity >= 30% below peak   -> half risk until back within 15% of peak
  equity >= 50% below peak   -> stop 7 days, review, restart on the next protocol version
  max 2 new entries per UTC day (1 in the BTC half-risk band, and no re-entries)

"Consecutive" counts SL exits since the last TP; time stops and discretionary exits don't reset the streak.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..protocol import Breakers

DAY = 86400


def utc_day_start(ts: float) -> float:
    return ts // DAY * DAY


@dataclass(frozen=True)
class Exit:
    coin: str
    exit_type: str  # tp | sl | time | discretion | override | friday | mismatch | liquidated
    closed_at: float


@dataclass
class BreakerState:
    blocked: list[str] = field(default_factory=list)   # reasons no new entry is allowed
    half_risk: list[str] = field(default_factory=list)  # reasons risk is halved
    sl_streak: int = 0
    drawdown_pct: float = 0.0
    dd_half_active: bool = False   # carry forward (hysteresis)

    def status(self) -> str:
        if self.blocked:
            return "BLOCKED: " + "; ".join(self.blocked)
        if self.half_risk:
            return "half risk: " + "; ".join(self.half_risk)
        return "none active"


def sl_streak(exits: list[Exit]) -> tuple[int, float | None]:
    """(SL exits since the last TP, time of the latest of them)."""
    n, last = 0, None
    for e in sorted(exits, key=lambda e: e.closed_at, reverse=True):
        if e.exit_type == "tp":
            break
        if e.exit_type in ("sl", "liquidated"):
            n += 1
            last = last or e.closed_at
    return n, last


def evaluate(
    b: Breakers,
    *,
    exits: list[Exit],
    entries_today: list[str],          # coins of entries placed today (UTC)
    equity: float,
    peak_equity: float,
    dd_half_was_active: bool,
    dd_stop: dict | None,              # {"since": ts, "version": "3.0"} once equity fell 50%+ below peak
    last_review_at: float | None,
    protocol_version: str,
    btc_band: str | None,
    now: float,
) -> BreakerState:
    st = BreakerState()
    today = utc_day_start(now)

    if any(e.exit_type in ("sl", "liquidated") and e.closed_at >= today for e in exits):
        st.blocked.append("SL hit today - no new entries until 00:00 UTC")

    st.sl_streak, last_sl = sl_streak(exits)
    if st.sl_streak >= b.pause_after_sl_streak and last_sl is not None:
        until = last_sl + b.pause_h * 3600
        if now < until:
            st.blocked.append(f"{st.sl_streak} straight SLs - paused until {_fmt(until)}")
        elif not last_review_at or last_review_at < last_sl:
            st.blocked.append(f"{st.sl_streak} straight SLs - send 'review' before trading again")
    if st.sl_streak >= b.half_risk_after_sl_streak:
        st.half_risk.append(f"{st.sl_streak} straight SLs - half risk until the next TP")

    st.drawdown_pct = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
    if st.drawdown_pct >= b.dd_half_risk_pct:
        st.dd_half_active = True
    elif dd_half_was_active and st.drawdown_pct > b.dd_half_risk_until_within_pct:
        st.dd_half_active = True
    if st.dd_half_active:
        st.half_risk.append(f"equity {st.drawdown_pct:.0f}% below peak - half risk until within "
                            f"{b.dd_half_risk_until_within_pct:g}% of peak")

    if dd_stop:
        until = dd_stop["since"] + b.dd_stop_days * DAY
        if now < until:
            st.blocked.append(f"equity fell {b.dd_stop_pct:g}%+ below peak - stopped until {_fmt(until)}")
        elif not last_review_at or last_review_at < dd_stop["since"]:
            st.blocked.append("50% drawdown stop - send 'review' before restarting")
        elif protocol_version == dd_stop["version"]:
            st.blocked.append(f"50% drawdown stop - restart needs the next protocol version (still v{protocol_version})")
    elif st.drawdown_pct >= b.dd_stop_pct:
        st.blocked.append(f"equity {st.drawdown_pct:.0f}% below peak - 7-day stop starts now")

    limit = b.half_band_max_entries if btc_band == "half" else b.max_entries_per_day
    if len(entries_today) >= limit:
        st.blocked.append(f"{len(entries_today)} entries today (max {limit}"
                          + (" in the BTC half-risk band)" if btc_band == "half" else ")"))
    return st


def reentry_blocked(coin: str, entries_today: list[str], btc_band: str | None) -> bool:
    """BTC half-risk band: no re-entries into a coin already traded today."""
    return btc_band == "half" and coin in entries_today


def _fmt(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%a %d %b %H:%M UTC")
