"""Daily summary in the owner's format, by Lagos calendar day (WAT, UTC+1, no daylight saving)."""

from __future__ import annotations

import time

LAGOS = 3600  # UTC+1
DAY = 86400


def lagos_day(now: float, today: bool) -> tuple[float, float, str]:
    """(start, end) in UTC seconds of the Lagos day: today so far, or the day that just ended."""
    local = now + LAGOS
    start_local = local // DAY * DAY - (0 if today else DAY)
    return start_local - LAGOS, start_local - LAGOS + DAY, time.strftime("%a %d %b", time.gmtime(start_local))


def block(trades: list[dict]) -> str:
    if not trades:
        return "Today (Lagos time): no closed trades"
    n = len(trades)
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    pnl = sum(t.get("pnl") or 0 for t in trades)
    vol = sum((t.get("qty") or 0) * ((t.get("fill_px") or t.get("entry") or 0) + (t.get("exit_px") or 0)) for t in trades)
    last = max(t["closed_at"] for t in trades)
    return (f"Today (Lagos time): {n} trades, {wins} profitable ({wins / n * 100:.0f}%)\n"
            f"Profit {'+' if pnl >= 0 else '-'}${abs(pnl):.2f} after fees · traded ${vol:,.2f}\n"
            f"Last trade {time.strftime('%H:%M', time.gmtime(last + LAGOS))}")


def daily_summary(engine, scalp=None, today: bool = False, now: float | None = None) -> str:
    start, end, label = lagos_day(now or time.time(), today)
    pick = lambda store, since: [t for t in store.closed(since) if start <= t["closed_at"] < end]  # noqa: E731
    out = [f"HEVI {'so far today' if today else 'daily summary'} - {label} (Lagos time)", "",
           f"Normal trades ({'LIVE' if engine.live else 'paper'}):", block(pick(engine.store, engine._since()))]
    if scalp is not None:
        out += ["", "Scalp BTC (paper):", block(pick(scalp.store, scalp._since())), f"Scalp gate: {scalp.gate()[1]}"]
    return "\n".join(out)
