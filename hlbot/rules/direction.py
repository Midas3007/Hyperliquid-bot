"""Module 5 - direction. Long is the default but needs 4 of 5 triggers; shorts need 2+ of theirs. N/A = fail."""

from __future__ import annotations

from dataclasses import dataclass

from ..protocol import Direction
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


def long_triggers(d: Direction, *, buy_sell, whale_ls, funding_8h, btc_band, fng) -> list[Trigger]:
    return [
        _t("buy/sell > 3x", buy_sell, lambda v: v > d.long_buy_sell_min, "{:.2f}x"),
        _t("whale L/S > 1.5", whale_ls, lambda v: v > d.long_whale_min, "{:.2f}"),
        _t("funding 0.00-0.03%/8h", funding_8h,
           lambda v: d.long_funding_min_8h_pct <= v <= d.long_funding_max_8h_pct, "{:+.4f}%/8h"),
        _t("BTC normal band or better", btc_band, normal_or_better, "{}"),
        _t("F&G > 50", fng, lambda v: v > d.long_fng_min, "{}"),
    ]


def trend_short_triggers(d: Direction, *, buy_sell, whale_ls, funding_8h, btc_broke_4h_support, fng) -> list[Trigger]:
    sell_buy = None if buy_sell is None or buy_sell <= 0 else 1 / buy_sell
    return [
        _t("sell/buy > 3x", sell_buy, lambda v: v > d.short_sell_buy_min, "{:.2f}x"),
        _t("whale L/S < 0.7", whale_ls, lambda v: v < d.short_whale_max, "{:.2f}"),
        _t("funding <= -0.03%/8h", funding_8h, lambda v: v <= d.short_funding_max_8h_pct, "{:+.4f}%/8h"),
        _t("BTC breaking 4H support", btc_broke_4h_support, bool, "{}"),
        _t("F&G < 40", fng, lambda v: v < d.short_fng_max, "{}"),
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


def decide(d: Direction, long_t: list[Trigger], trend_t: list[Trigger], rev_t: list[Trigger]) -> tuple[list[str], str]:
    """Sides to set up, and why. Reversal 2+ -> show long and short (if long qualifies) and let the user choose."""
    nl, nt, nr = (sum(t.hit for t in x) for x in (long_t, trend_t, rev_t))
    long_ok = nl >= d.long_needed
    trend = nt >= d.trend_short_needed
    rev = nr >= d.reversal_needed
    tally = f"long {nl}/5 (need {d.long_needed}), trend short {nt}/5, reversal short {nr}/5"
    if rev:
        return (["long", "short"] if long_ok else ["short"]), f"reversal signals present - {tally}"
    if trend and long_ok:
        return [], f"long and trend-short both qualify: conflicting, skip - {tally}"
    if trend:
        return ["short"], f"trend short - {tally}"
    if long_ok:
        return ["long"], f"long - {tally}"
    return [], f"no direction qualifies - {tally}"
