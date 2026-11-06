"""Module 6 (entry at 5m confluence), Module 7 (SL placement) and Module 4 (TP rule)."""

from __future__ import annotations

from dataclasses import dataclass

from ..levels import Level, fmt
from ..protocol import Entry, Risk, Tp


@dataclass(frozen=True)
class Lvl:
    price: float
    label: str
    methods: tuple


def merge(levels: list[Level], merge_pct: float) -> list[Lvl]:
    """Levels within `merge_pct` of each other are one level (avoids counting one zone several times)."""
    out: list[Lvl] = []
    group: list[Level] = []
    for lv in sorted(levels, key=lambda x: x.price):
        if group and (lv.price / group[0].price - 1) * 100 > merge_pct:
            out.append(_lvl(group))
            group = []
        group.append(lv)
    if group:
        out.append(_lvl(group))
    return out


def _lvl(g: list[Level]) -> Lvl:
    px = sum(x.price for x in g) / len(g)
    names = " + ".join(sorted({f"{x.method} {x.name}" for x in g}))
    return Lvl(px, f"{g[0].tf} {names} {fmt(px)}", tuple(sorted({x.method for x in g})))


def structural(levels_4h: list[Level], t: Tp) -> list[Lvl]:
    """Real 4H structure: the methods listed in protocol.toml [tp].structural_methods, merged into zones."""
    return merge([x for x in levels_4h if x.method in t.structural_methods], t.level_merge_pct)


@dataclass(frozen=True)
class Confluence:
    price: float
    methods: tuple
    members: tuple


def entry_confluence(levels_5m: list[Level], price: float, side: str, e: Entry) -> tuple[Confluence | None, str]:
    """Best 5m cluster on the passive side of price (below for longs, above for shorts) so a post-only (ALO)
    limit rests instead of crossing. Needs `min_methods` distinct methods within `confluence_tol_pct`."""
    d = e.max_distance_pct / 100
    pool = [x for x in levels_5m if x.method in e.confluence_methods]
    if side == "long":
        pool = [x for x in pool if price * (1 - d) <= x.price < price]
    else:
        pool = [x for x in pool if price < x.price <= price * (1 + d)]
    best: Confluence | None = None
    for a in pool:
        members = [x for x in pool if abs(x.price - a.price) / a.price * 100 <= e.confluence_tol_pct]
        methods = tuple(sorted({x.method for x in members}))
        if len(methods) < e.min_methods:
            continue
        px = sum(x.price for x in members) / len(members)
        c = Confluence(px, methods, tuple(f"{x.method} {x.name}" for x in members))
        if best is None or (len(c.methods), -abs(price - c.price)) > (len(best.methods), -abs(price - best.price)):
            best = c
    if not best:
        return None, (f"no 5m level where {e.min_methods} of {'/'.join(e.confluence_methods)} agree "
                      f"within {e.max_distance_pct:g}% of price")
    return best, "ok"


def stop_loss(entry: float, side: str, struct: list[Lvl], atr_4h: float | None, r: Risk) -> tuple[float | None, str]:
    """Beyond the nearest 4H structural level by >= 0.25x 4H ATR, and never closer than 1.5% to entry."""
    if atr_4h is None:
        return None, "N/A: no 4H ATR"
    if side == "long":
        below = [x for x in struct if x.price < entry]
        if not below:
            return None, "no 4H structure below entry"
        anchor = max(below, key=lambda x: x.price)
        sl_struct = anchor.price - r.sl_atr_buffer_mult * atr_4h
        floor = entry * (1 - r.sl_min_pct / 100)
        sl = min(sl_struct, floor)
    else:
        above = [x for x in struct if x.price > entry]
        if not above:
            return None, "no 4H structure above entry"
        anchor = min(above, key=lambda x: x.price)
        sl_struct = anchor.price + r.sl_atr_buffer_mult * atr_4h
        floor = entry * (1 + r.sl_min_pct / 100)
        sl = max(sl_struct, floor)
    how = "past " + anchor.label + (" (widened to the 1.5% floor)" if sl == floor and sl != sl_struct else "")
    return sl, how


@dataclass(frozen=True)
class TpPlan:
    tp: float
    level: Lvl
    dist_pct: float
    adr_mult: float


def take_profit(entry: float, price: float, side: str, struct: list[Lvl], atr_4h_pct: float | None,
                adr_pct: float | None, t: Tp) -> tuple[TpPlan | None, str]:
    """TP just in front of the first real 4H level in the trade's direction (clean air: nothing in between),
    pulled in by max(0.2%, 0.1x 4H ATR), and no further than 1.2x the 14-day ADR. Never a 1D level."""
    if atr_4h_pct is None:
        return None, "N/A: no 4H ATR"
    if adr_pct is None:
        return None, "N/A: no 14-day ADR"
    front = max(t.front_run_min_pct, t.front_run_atr_mult * atr_4h_pct) / 100
    if side == "long":
        ref = max(entry, price)
        ahead = [x for x in struct if x.price > ref]
        if not ahead:
            return None, "no 4H resistance above price to anchor the TP"
        lvl = min(ahead, key=lambda x: x.price)
        tp = lvl.price * (1 - front)
        dist = (tp / entry - 1) * 100
    else:
        ref = min(entry, price)
        ahead = [x for x in struct if x.price < ref]
        if not ahead:
            return None, "no 4H support below price to anchor the TP"
        lvl = max(ahead, key=lambda x: x.price)
        tp = lvl.price * (1 + front)
        dist = (1 - tp / entry) * 100
    if dist <= 0:
        return None, f"first 4H level {lvl.label} is too close to entry"
    plan = TpPlan(tp, lvl, dist, dist / adr_pct)
    if dist > t.max_adr_mult * adr_pct:
        return None, f"TP {dist:.2f}% away = {plan.adr_mult:.2f}x ADR (max {t.max_adr_mult:g}x): not a 1-2 day target"
    return plan, "ok"
