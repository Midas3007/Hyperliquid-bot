"""Module 5 - direction, per strategy mode.

swing (protocol): long needs 4 of 5 triggers; a trigger with no data counts as a miss.
day: long needs 3 of the measurable triggers, and at least 4 of 5 must be measurable. A trigger with no data for the
coin (for example, no tracked top trader holds it) is left out instead of counted as a miss.
Reversal shorts keep the protocol's 2+ and only use measurable triggers in day mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..protocol import Direction, Mode
from .gate import normal_or_better


@dataclass(frozen=True)
class Trigger:
    name: str
    hit: bool
    detail: str


def _t(name: str, value, test, fmt: str) -> Trigger:
    if value is None:
        return Trigger(name, False, "N/A")
    return Trigger(name, bool(test(value)), fmt.format(value))


def long_triggers(d: Direction, m: Mode, *, buy_sell, whale_ls, funding_8h, btc_band, fng) -> list[Trigger]:
    return [
        _t(f"buy/sell > {m.long_buy_sell_min:g}x", buy_sell, lambda v: v > m.long_buy_sell_min, "{:.2f}x"),
        _t(f"whale L/S > {d.long_whale_min:g}", whale_ls, lambda v: v > d.long_whale_min, "{:.2f}"),
        _t("funding 0.00-0.03%/8h", funding_8h,
           lambda v: d.long_funding_min_8h_pct <= v <= d.long_funding_max_8h_pct, "{:+.4f}%/8h"),
        _t("BTC normal band or better", btc_band, normal_or_better, "{}"),
        _t(f"F&G > {d.long_fng_min:g}", fng, lambda v: v > d.long_fng_min, "{}"),
    ]


def trend_short_triggers(d: Direction, m: Mode, *, buy_sell, whale_ls, funding_8h, btc_broke_4h_support, fng) -> list[Trigger]:
    sell_buy = None if buy_sell is None or buy_sell <= 0 else 1 / buy_sell
    return [
        _t(f"sell/buy > {m.short_sell_buy_min:g}x", sell_buy, lambda v: v > m.short_sell_buy_min, "{:.2f}x"),
        _t(f"whale L/S < {d.short_whale_max:g}", whale_ls, lambda v: v < d.short_whale_max, "{:.2f}"),
        _t("funding <= -0.03%/8h", funding_8h, lambda v: v <= d.short_funding_max_8h_pct, "{:+.4f}%/8h"),
        _t("BTC breaking 4H support", btc_broke_4h_support, bool, "{}"),
        _t(f"F&G < {d.short_fng_max:g}", fng, lambda v: v < d.short_fng_max, "{}"),
    ]


def reversal_short_triggers(
    d: Direction, *, rejection_wick, funding_8h, funding_rising, oi_change_24h, day_change,
    whale_ls_now, whale_ls_24h_ago, btc_vol_now, btc_vol_24h_ago, btc_change,
) -> list[Trigger]:
    out = [_t("rejection wick at 1D/4H resistance", rejection_wick, bool, "{}")]
    if funding_8h is None or funding_rising is None:
        out.append(Trigger("funding >= 0.03%/8h and rising", False, "N/A"))
    else:
        out.append(Trigger("funding >= 0.03%/8h and rising", funding_8h >= d.reversal_funding_8h_pct and funding_rising,
                           f"{funding_8h:+.4f}%/8h, {'rising' if funding_rising else 'not rising'}"))
    if oi_change_24h is None or day_change is None:
        out.append(Trigger("OI dropping on a spike", False, "N/A"))
    else:
        out.append(Trigger("OI dropping on a spike",
                           oi_change_24h <= d.reversal_oi_drop_pct and day_change >= d.reversal_spike_pct,
                           f"OI {oi_change_24h:+.1f}% on {day_change:+.1f}%"))
    if whale_ls_now is None or whale_ls_24h_ago is None or day_change is None:
        out.append(Trigger("whale L/S flipping < 1 after a pump", False, "N/A"))
    else:
        out.append(Trigger("whale L/S flipping < 1 after a pump",
                           whale_ls_24h_ago >= 1 > whale_ls_now and day_change >= d.reversal_pump_pct,
                           f"{whale_ls_24h_ago:.2f} -> {whale_ls_now:.2f} on {day_change:+.1f}%"))
    if btc_vol_now is None or btc_vol_24h_ago is None or btc_change is None:
        out.append(Trigger("BTC IV jump after a fast +3%", False, "N/A"))
    else:
        hot = ("HIGH", "EXTREME")
        out.append(Trigger("BTC IV jump after a fast +3%",
                           btc_vol_now in hot and btc_vol_24h_ago not in hot and btc_change >= d.reversal_btc_fast_move_pct,
                           f"{btc_vol_24h_ago} -> {btc_vol_now} on BTC {btc_change:+.1f}%"))
    return out


def decide(d: Direction, m: Mode, long_t: list[Trigger], trend_t: list[Trigger],
           rev_t: list[Trigger]) -> tuple[list[str], str]:
    """Sides to set up, and why. Reversal 2+ -> long and short are both shown (long only if long qualifies)."""
    def count(trigs: list[Trigger], needed: int, min_meas: int) -> tuple[bool, int, int]:
        hits = sum(t.hit for t in trigs)
        meas = sum(t.detail != "N/A" for t in trigs)
        ok = hits >= needed and (m.na_is_miss or meas >= min_meas)
        return ok, hits, meas

    long_ok, nl, ml = count(long_t, m.long_needed, m.min_measurable)
    trend, nt, mt = count(trend_t, m.trend_short_needed, m.min_measurable_short)
    rev, nr, mr = count(rev_t, d.reversal_needed, m.min_measurable_short)
    tally = (f"long {nl}/5 ({ml} measurable, need {m.long_needed}), trend short {nt}/5 ({mt} measurable), "
             f"reversal short {nr}/5 ({mr} measurable)")
    if rev:
        return (["long", "short"] if long_ok else ["short"]), f"reversal signals present - {tally}"
    if trend and long_ok:
        return [], f"long and trend-short both qualify: conflicting, skip - {tally}"
    if trend:
        return ["short"], f"trend short - {tally}"
    if long_ok:
        return ["long"], f"long - {tally}"
    return [], f"no direction qualifies - {tally}"
