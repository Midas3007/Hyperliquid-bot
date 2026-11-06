"""Performance statistics over closed trades - the track record behind /stats.

R-multiple = realized PnL / planned risk (qty x |entry - SL|). +3R means the trade made three times what it risked.
Expectancy is the average PnL per trade; it has to be positive over a meaningful sample (30+ trades) before the
strategy deserves real money.
"""

from __future__ import annotations

import math


def summarize(trades: list[dict], start_balance: float | None = None) -> dict:
    """`trades`: closed trade rows from the store, oldest first."""
    rows = [t for t in trades if t.get("pnl") is not None]
    pnls = [float(t["pnl"]) for t in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    rs = []
    for t in rows:
        risk = abs(float(t["entry"]) - float(t["sl"])) * float(t["qty"]) if t.get("entry") and t.get("sl") else 0
        if risk > 0:
            rs.append(float(t["pnl"]) / risk)

    # equity curve + max drawdown
    equity = start_balance or 0.0
    peak, max_dd, max_dd_pct = equity, 0.0, 0.0
    streak = worst_streak = 0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100 if peak > 0 and start_balance else 0.0
        streak = streak + 1 if p <= 0 else 0
        worst_streak = max(worst_streak, streak)

    reasons: dict[str, int] = {}
    for t in rows:
        reasons[t.get("close_reason") or "unknown"] = reasons.get(t.get("close_reason") or "unknown", 0) + 1

    n = len(pnls)
    total = sum(pnls)
    gross_loss = -sum(losses)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n * 100 if n else 0.0,
        "total_pnl": total,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else (math.inf if wins else 0.0),
        "expectancy": total / n if n else 0.0,
        "avg_r": sum(rs) / len(rs) if rs else None,
        "best": max(pnls) if pnls else 0.0,
        "worst": min(pnls) if pnls else 0.0,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "worst_losing_streak": worst_streak,
        "start_balance": start_balance,
        "end_balance": (start_balance + total) if start_balance else None,
        "return_pct": total / start_balance * 100 if start_balance else None,
        "close_reasons": reasons,
        "first_ts": rows[0].get("closed_at") if rows else None,
        "last_ts": rows[-1].get("closed_at") if rows else None,
    }


def verdict(s: dict) -> str:
    if s["trades"] < 30:
        return f"Sample too small to judge ({s['trades']}/30 trades). Keep paper trading."
    if s["expectancy"] <= 0:
        return "Negative expectancy - the strategy is losing money. Do not go live."
    if s["profit_factor"] < 1.3:
        return "Barely positive (profit factor < 1.3) - fees and slippage could erase it live."
    return "Positive expectancy over 30+ trades. Go live small, and keep comparing live vs paper."


def format_stats(s: dict, label: str) -> str:
    if not s["trades"]:
        return f"{label}\nNo closed trades yet."
    pf = "inf" if math.isinf(s["profit_factor"]) else f"{s['profit_factor']:.2f}"
    out = [
        label,
        f"Trades {s['trades']} | wins {s['wins']} | losses {s['losses']} | win rate {s['win_rate']:.0f}%",
        f"Total PnL ${s['total_pnl']:+.2f} | expectancy ${s['expectancy']:+.2f}/trade"
        + (f" | avg {s['avg_r']:+.2f}R" if s["avg_r"] is not None else ""),
        f"Avg win ${s['avg_win']:+.2f} | avg loss ${s['avg_loss']:+.2f} | profit factor {pf}",
        f"Best ${s['best']:+.2f} | worst ${s['worst']:+.2f} | worst losing streak {s['worst_losing_streak']}",
        f"Max drawdown ${s['max_drawdown']:.2f}" + (f" ({s['max_drawdown_pct']:.1f}%)" if s["start_balance"] else ""),
    ]
    if s["start_balance"]:
        out.append(f"Balance ${s['start_balance']:.2f} -> ${s['end_balance']:.2f} ({s['return_pct']:+.1f}%)")
    out.append("Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(s["close_reasons"].items(), key=lambda x: -x[1])))
    out.append(verdict(s))
    return "\n".join(out)
