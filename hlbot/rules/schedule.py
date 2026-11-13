"""Module 9 - trade management windows (all UTC), Module 0 macro-event check, Module 6 cancel triggers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..protocol import Entry, Schedule

H = 3600


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


def entry_window_open(now: float, s: Schedule) -> bool:
    """Entries Mon 00:00 to Thu 23:59 UTC."""
    return utc(now).weekday() in s.entry_weekdays


def next_friday_flat(now: float, s: Schedule) -> float:
    d = utc(now)
    days = (4 - d.weekday()) % 7
    fri = (d + timedelta(days=days)).replace(hour=s.friday_flat_hour, minute=0, second=0, microsecond=0)
    if fri <= d:
        fri += timedelta(days=7)
    return fri.timestamp()


def must_cancel_entries(now: float, s: Schedule) -> bool:
    """Friday 00:00 UTC onward (and the weekend): unfilled entries are cancelled."""
    d = utc(now)
    return d.weekday() >= 5 or (d.weekday() == 4 and d.hour >= s.friday_cancel_hour)


def must_be_flat(now: float, s: Schedule) -> bool:
    d = utc(now)
    return d.weekday() >= 5 or (d.weekday() == 4 and d.hour >= s.friday_flat_hour)


def time_stop_at(filled_at: float, s: Schedule, hold_h: float | None = None) -> float:
    """The mode's hold after the fill (48h swing, 24h day), or the Friday flatten, whichever comes first."""
    return min(filled_at + (hold_h or s.time_stop_h) * H, next_friday_flat(filled_at, s))


def planned_hold(now: float, s: Schedule, e: Entry, hold_h: float | None = None) -> tuple[float, float]:
    """Window a new trade could be open: up to the entry TTL to fill, then the time stop, capped by Friday flat."""
    latest_fill = now + e.cancel_after_h * H
    return now, min(latest_fill + (hold_h or s.time_stop_h) * H, next_friday_flat(now, s))


def macro_in_window(events: list[tuple[str, float]], covered_until: float | None, start: float, end: float,
                    s: Schedule) -> tuple[list[tuple[str, float]] | None, str]:
    """High-impact US releases inside [start, end]. None = calendar doesn't cover the window (missing = FAIL)."""
    if covered_until is None or covered_until < end:
        return None, "macro calendar does not cover the planned hold (update calendar.toml)"
    hits = [(n, t) for n, t in events if n in s.macro_events and start <= t <= end]
    return hits, "ok"


def cancel_triggers(
    e: Entry,
    *,
    side: str,
    created_at: float,
    now: float,
    entry: float,
    sl: float,
    tp: float,
    price: float,
    band_at_order: str | None,
    band_now: str | None,
    last_4h_close: float | None,
    stricter: bool,
) -> list[str]:
    """Reasons to cancel an unfilled (or the rest of a partially filled) entry. Never chase."""
    out = []
    if now - created_at >= e.cancel_after_h * H:
        out.append(f"{e.cancel_after_h:g}h passed unfilled")
    if band_now is None:
        out.append("BTC gate data missing")
    elif stricter:
        out.append(f"BTC moved into a stricter band ({band_at_order} -> {band_now})")
    s = 1 if side == "long" else -1
    if s * (tp - entry) > 0 and s * (price - entry) / (s * (tp - entry)) >= e.cancel_progress_to_tp:
        out.append("price covered half the way to TP without filling (R/R gone)")
    if last_4h_close is not None and s * (sl - last_4h_close) >= 0:
        out.append("a 4H candle closed beyond the SL level")
    return out


def early_exit_suggestions(side: str, btc_change: float | None, macro_ahead: list[tuple[str, float]] | None,
                           s: Schedule) -> list[str]:
    """Objective early-exit triggers the bot may suggest (never executes without the user)."""
    out = []
    if btc_change is not None:
        if side == "long" and btc_change <= -s.early_exit_btc_pct:
            out.append(f"BTC {btc_change:+.1f}% today (<= -{s.early_exit_btc_pct:g}%)")
        if side == "short" and btc_change >= s.early_exit_btc_pct:
            out.append(f"BTC {btc_change:+.1f}% today (>= +{s.early_exit_btc_pct:g}%)")
    for name, t in macro_ahead or []:
        out.append(f"{name} at {utc(t):%a %H:%M} UTC inside the remaining hold")
    return out
