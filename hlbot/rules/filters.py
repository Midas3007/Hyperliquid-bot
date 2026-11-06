"""Module 3 - hard filters (any fail = skip) and ranking of survivors.

Shorts mirror the directional filters: whale L/S at or below 1/1.5, 2+ of 3 news bearish, not UP more than 8%.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..protocol import Filters, Ranking, Screener
from .checks import Check, na_check


@dataclass(frozen=True)
class News:
    bullish: int
    bearish: int
    total: int                  # articles rated (the protocol looks at the last 3)
    catalyst: str | None        # thesis-breaking catalyst inside the hold window, if any


def hard_filters(
    side: str,
    f: Filters,
    s: Screener,
    *,
    day_change_pct: float | None,
    whale_ls: float | None,
    whale_is_true: bool,
    crowd_ls: float | None,
    oi_change_24h_pct: float | None,
    vol_label: str | None,
    news: News | None,
) -> list[Check]:
    out: list[Check] = []
    long = side == "long"

    if day_change_pct is None:
        out.append(na_check("M3", "24h price", "no UTC daily open"))
    else:
        bad = day_change_pct < -s.max_day_drop_pct if long else day_change_pct > s.max_day_drop_pct
        note = " (red day: caution)" if long and day_change_pct < 0 else ""
        out.append(Check("M3", "24h price", not bad, f"{day_change_pct:+.2f}% on the UTC day{note}"))

    if whale_is_true and whale_ls is not None:
        ok = whale_ls >= f.whale_min_ratio if long else whale_ls <= 1 / f.whale_min_ratio
        need = f">= {f.whale_min_ratio:g}" if long else f"<= {1 / f.whale_min_ratio:.2f}"
        out.append(Check("M3", "whale L/S", ok, f"whale L/S {whale_ls:.2f} (need {need})"))
    elif crowd_ls is not None:
        ok = crowd_ls <= f.crowd_max_ratio if long else crowd_ls >= 1 / f.crowd_max_ratio
        out.append(Check("M3", "crowd L/S", ok, f"crowd L/S {crowd_ls:.2f} (whale data N/A)"))
    else:
        out.append(na_check("M3", "whale L/S", "no whale or crowd positioning data"))

    if oi_change_24h_pct is None:
        out.append(na_check("M3", "OI 24h", "no OI snapshot from 24h ago yet"))
    else:
        ok = oi_change_24h_pct >= -f.oi_flat_tolerance_pct
        out.append(Check("M3", "OI 24h", ok, f"OI {oi_change_24h_pct:+.1f}% in 24h (growing or flat needed)"))

    if vol_label is None:
        out.append(na_check("M3", "volatility", "no IV/RV percentile"))
    else:
        out.append(Check("M3", "volatility", vol_label in ("LOW", "NORMAL", "HIGH"),
                         vol_label + (" (caution)" if vol_label == "HIGH" else ""), hard=vol_label == "EXTREME"))

    if news is None or news.total == 0:
        out.append(na_check("M3", "news", "no recent articles for this coin"))
    else:
        good = news.bullish if long else news.bearish
        word = "bullish" if long else "bearish"
        ok = good >= f.news_min_bullish and not news.catalyst
        why = f"{good}/{news.total} {word}" + (f"; catalyst in hold: {news.catalyst}" if news.catalyst else "")
        out.append(Check("M3", "news", ok, why))
    return out


def rank_score(
    side: str,
    rk: Ranking,
    *,
    whale_ls: float | None,
    oi_growth_30d_pct: float | None,
    news: News | None,
    vol_label: str | None,
    net_rr: float,
    tp_adr_mult: float,
) -> tuple[int, list[str]]:
    """+1 each: whale L/S >= 2:1, 30d OI growth > 0, news 3/3, LOW volatility, net R/R >= 1:4, TP <= 1.0x ADR."""
    pts: list[str] = []
    long = side == "long"
    if whale_ls is not None and (whale_ls >= rk.whale_ratio if long else whale_ls <= 1 / rk.whale_ratio):
        pts.append("whale L/S")
    if oi_growth_30d_pct is not None and oi_growth_30d_pct > 0:
        pts.append("30d OI growth")
    if news and news.total >= 3 and (news.bullish if long else news.bearish) >= 3:
        pts.append("news 3/3")
    if vol_label == "LOW":
        pts.append("LOW vol")
    if net_rr >= rk.net_rr:
        pts.append("net R/R >= 1:4")
    if tp_adr_mult <= rk.tp_adr_mult:
        pts.append("TP <= 1.0x ADR")
    return len(pts), pts
