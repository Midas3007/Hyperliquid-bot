"""Module 11 - results in R, the 20-trade review, risk recalibration and the discretion comparison.

R = the dollar loss planned at the SL, costs included. result_r = result_usd / risk_usd.
Recalibrated risk = kelly_fraction x (p - (1 - p) / b), p = hit rate, b = avg win R / avg loss R (last 30 trades),
capped at the 15% ceiling. Under 2% = no edge: stop and rework the screener.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..protocol import Review, Risk


@dataclass(frozen=True)
class LoggedTrade:
    result_usd: float
    result_r: float
    exit_type: str               # tp | sl | time | discretion | override | friday | mismatch | liquidated
    override: bool = False
    mae_r: float | None = None   # largest move against, in R (negative)
    mfe_r: float | None = None   # largest move for, in R
    bracket_r: float | None = None  # discretionary exits: what the TP/SL bracket would have made


def summarize(trades: list[LoggedTrade], rv: Review, rk: Risk) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0}
    wins = [t for t in trades if t.result_r > 0]
    losses = [t for t in trades if t.result_r <= 0]
    hit = len(wins) / n
    avg_win = sum(t.result_r for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.result_r for t in losses) / len(losses) if losses else 0.0

    eq = peak = dd = 0.0
    streak = longest = 0
    for t in trades:
        eq += t.result_r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if t.result_r <= 0 else 0
        longest = max(longest, streak)

    by_exit: dict[str, list[float]] = {}
    for t in trades:
        by_exit.setdefault(t.exit_type, []).append(t.result_r)

    disc = [t for t in trades if t.exit_type == "discretion"]
    disc_vs = None
    if disc:
        known = [t for t in disc if t.bracket_r is not None]
        disc_vs = {"n": len(disc), "actual_r": sum(t.result_r for t in known),
                   "bracket_r": sum(t.bracket_r for t in known), "compared": len(known)}
    over = [t for t in trades if t.override]

    last = trades[-rv.recalibrate_lookback:]
    rec = recalibrate(last, rv, rk) if len(last) >= rv.recalibrate_lookback else None
    return {
        "trades": n,
        "hit_rate": hit * 100,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "expectancy_r": sum(t.result_r for t in trades) / n,
        "total_r": sum(t.result_r for t in trades),
        "total_usd": sum(t.result_usd for t in trades),
        "max_drawdown_r": dd,
        "longest_losing_streak": longest,
        "by_exit": {k: (len(v), sum(v)) for k, v in by_exit.items()},
        "discretion": disc_vs,
        "overrides": (len(over), sum(t.result_r for t in over)) if over else None,
        "mae_avg_r": _avg([t.mae_r for t in trades]),
        "mfe_losers_avg_r": _avg([t.mfe_r for t in losses]),
        "losers_green_first": sum(1 for t in losses if t.mfe_r is not None and t.mfe_r > 0),
        "recalibrated_risk_pct": rec,
    }


def recalibrate(trades: list[LoggedTrade], rv: Review, rk: Risk) -> float | None:
    wins = [t.result_r for t in trades if t.result_r > 0]
    losses = [-t.result_r for t in trades if t.result_r <= 0]
    if not trades or not wins or not losses:
        return None
    p = len(wins) / len(trades)
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    if b <= 0:
        return 0.0
    k = rv.kelly_fraction * (p - (1 - p) / b) * 100
    return max(0.0, min(rk.ceiling_pct, k))


def discretion_restricted(trades: list[LoggedTrade], rv: Review) -> bool:
    """After 10 discretionary exits, discretion that made less than the bracket would have gets restricted."""
    disc = [t for t in trades if t.exit_type == "discretion" and t.bracket_r is not None]
    if len(disc) < rv.discretion_compare_after:
        return False
    return sum(t.result_r for t in disc) < sum(t.bracket_r for t in disc)


def _avg(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def format_review(s: dict, label: str, rv: Review) -> str:
    if not s.get("trades"):
        return f"{label}\nNo closed trades yet."
    out = [
        label,
        f"Trades {s['trades']} | hit rate {s['hit_rate']:.0f}% | expectancy {s['expectancy_r']:+.2f}R/trade",
        f"Avg win {s['avg_win_r']:+.2f}R | avg loss {s['avg_loss_r']:+.2f}R | total {s['total_r']:+.2f}R (${s['total_usd']:+.2f})",
        f"Max drawdown {s['max_drawdown_r']:.2f}R | longest losing streak {s['longest_losing_streak']}",
        "Exits: " + ", ".join(f"{k} {n} ({r:+.1f}R)" for k, (n, r) in sorted(s["by_exit"].items())),
    ]
    if s["discretion"]:
        d = s["discretion"]
        out.append(f"Discretionary exits {d['n']}: actual {d['actual_r']:+.2f}R vs bracket {d['bracket_r']:+.2f}R "
                   f"({d['compared']} compared)")
    if s["overrides"]:
        out.append(f"Overrides: {s['overrides'][0]} trades, {s['overrides'][1]:+.2f}R")
    if s["mae_avg_r"] is not None:
        out.append(f"MAE avg {s['mae_avg_r']:+.2f}R (are stops inside normal noise?)")
    if s["mfe_losers_avg_r"] is not None:
        out.append(f"MFE on losers avg {s['mfe_losers_avg_r']:+.2f}R; {s['losers_green_first']} losers went green first")
    rec = s["recalibrated_risk_pct"]
    if rec is None:
        out.append(f"Risk recalibration needs {rv.recalibrate_lookback} trades.")
    elif rec < rv.no_edge_below_pct:
        out.append(f"Recalibrated risk {rec:.1f}% < {rv.no_edge_below_pct:g}%: NO EDGE. Stop and rework the screener.")
    else:
        out.append(f"Recalibrated risk from the last {rv.recalibrate_lookback} trades: {rec:.1f}% (set it with 'risk X%')")
    if s["trades"] < rv.every_n_trades:
        out.append(f"Rule changes wait for the {rv.every_n_trades}-trade review ({s['trades']}/{rv.every_n_trades}).")
    return "\n".join(out)
