"""Modules 1-7 + 10: BTC gate -> screener -> deep dive -> direction -> entry/SL/TP -> sizing -> proposal."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import indicators as ind
from .config import Config
from .levels import Level, Setup, build_levels, fmt, nearest, plan_setup
from .market import Market
from .risk import Sizing, SizingInput, size_position
from .store import Store

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- Module 1
@dataclass
class Gate:
    state: str  # skip | reduced | normal | sizeup
    btc_change: float
    btc_price: float
    btc_break_support: bool
    btc_iv: dict | None
    detail: str
    pump_check: dict | None = None


def btc_gate(cfg: Config, mkt: Market) -> Gate:
    btc = mkt.ctxs()["BTC"]
    ch = btc["change_pct"]
    if ch < cfg.btc_skip_below_pct:
        state = "skip"
    elif ch < cfg.btc_reduced_below_pct:
        state = "reduced"
    elif ch > cfg.btc_sizeup_above_pct:
        state = "sizeup"
    else:
        state = "normal"
    c4h = mkt.candles("BTC", "4h", 180)
    c1d = mkt.candles("BTC", "1d", 90)
    lv = build_levels(mkt.candles("BTC", "5m", 300), c4h, c1d)
    supports = [l for l in lv["4h"] if (l.method, l.name) in {("BB", "lower"), ("VP", "VAL"), ("FIB", "S1")}]
    broke = any(btc["mark"] < l.price for l in supports)
    detail = {
        "skip": f"BTC {ch:+.2f}% (< {cfg.btc_skip_below_pct}%) -> HARD SKIP",
        "reduced": f"BTC {ch:+.2f}% -> max ${cfg.btc_reduced_margin_usd:.2f} margin",
        "normal": f"BTC {ch:+.2f}% -> proceed normally",
        "sizeup": f"BTC {ch:+.2f}% (> {cfg.btc_sizeup_above_pct}%) -> can size up + reversal check",
    }[state]
    g = Gate(state, ch, btc["mark"], broke, mkt.iv("BTC"), detail)
    if ch > cfg.btc_pump_check_pct:
        g.pump_check = post_pump_check(cfg, mkt, "BTC", lv)
    return g


def post_pump_check(cfg: Config, mkt: Market, coin: str, lv: dict[str, list[Level]] | None = None) -> dict:
    """Funding, whale L/S, OI delta, nearest resistance -> flag building reversal."""
    d = mkt.ctxs()[coin]
    if lv is None:
        lv = build_levels(mkt.candles(coin, "5m", 300), mkt.candles(coin, "4h", 180), mkt.candles(coin, "1d", 90))
    whale = mkt.whales().get(coin, {})
    oi = mkt.oi_trend(coin, d["oi_usd"])
    res = nearest(lv["4h"] + lv["1d"], d["mark"], above=True)
    flags = []
    if d["funding_8h_pct"] >= cfg.funding_cap_8h_pct:
        flags.append(f"funding hot {d['funding_8h_pct']:.4f}%/8h")
    if whale.get("ratio") is not None and whale["ratio"] < 1:
        flags.append(f"whales net short (L/S {whale['ratio']:.2f})")
    if oi.get("delta_24h_pct") is not None and oi["delta_24h_pct"] < 0:
        flags.append(f"OI falling on the pump ({oi['delta_24h_pct']:+.1f}%)")
    if res and (res.price / d["mark"] - 1) * 100 < 1.0:
        flags.append(f"price within 1% of {res.label()}")
    return {
        "coin": coin,
        "change_pct": d["change_pct"],
        "funding_8h_pct": d["funding_8h_pct"],
        "whale_ls": whale.get("ratio"),
        "oi": oi,
        "nearest_resistance": res.label() if res else None,
        "flags": flags,
        "reversal_building": len(flags) >= 2,
    }


# ---------------------------------------------------------------- Modules 2-4
@dataclass
class Candidate:
    coin: str
    ctx: dict
    flow: dict | None
    whale: dict | None
    oi: dict
    iv: dict | None
    news: list[dict]
    levels: dict[str, list[Level]]
    long_triggers: list[str] = field(default_factory=list)
    trend_short: list[str] = field(default_factory=list)
    reversal_short: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    base_score: float = 0.0
    tag: str = "top buy/sell"


def screener(cfg: Config, mkt: Market, store: Store, friday: bool, mode: str) -> tuple[list[str], list[str], dict]:
    """Returns (candidate coins, skipped notes, buy/sell ratios)."""
    ctxs = mkt.ctxs()
    notes: list[str] = []
    day_start = datetime.now(cfg.tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    pool = []
    for coin, d in ctxs.items():
        if d["volume_usd"] < cfg.min_volume_usd:
            continue
        if coin.upper() in cfg.skip_coins:
            notes.append(f"{coin}: skip list")
            continue
        if d["change_pct"] < cfg.max_day_drop_pct:
            notes.append(f"{coin}: down {d['change_pct']:.1f}% on the day")
            continue
        if friday and d["change_pct"] > cfg.friday_max_pump_pct:
            notes.append(f"{coin}: +{d['change_pct']:.1f}% pump on a Friday")
            continue
        if mode == "A" and d["max_leverage"] < cfg.min_leverage_mode_a:
            continue  # cannot satisfy the 15x floor
        if store.losses_today(coin, day_start) > 0:
            notes.append(f"{coin}: already lost on it today (no re-entry on a red day)")
            continue
        pool.append(coin)
    mkt.flow.ensure(pool)
    ratios = {c: mkt.buy_sell(c) for c in pool}
    ranked = sorted([c for c in pool if ratios[c]], key=lambda c: ratios[c]["ratio"], reverse=True)

    picked: list[str] = []
    for c in ranked:
        iv = mkt.iv(c)
        if iv and iv["label"] == "EXTREME":
            notes.append(f"{c}: IV rank EXTREME")
            continue
        picked.append(c)
        if len(picked) >= cfg.candidates:
            break
    # Also deep-dive the heaviest sell-flow names so trend-short triggers can be evaluated.
    for c in reversed(ranked):
        if len(picked) >= cfg.candidates + 2 or c in picked:
            break
        if ratios[c]["ratio"] < 1 / 3:
            picked.append(c)
    return picked, notes, ratios


def deep_dive(cfg: Config, mkt: Market, coin: str, gate: Gate, fng: dict | None, ratios: dict) -> Candidate:
    d = mkt.ctxs()[coin]
    c5 = mkt.candles(coin, "5m", 300)
    c4 = mkt.candles(coin, "4h", 180)
    c1 = mkt.candles(coin, "1d", 90)
    cand = Candidate(
        coin=coin,
        ctx=d,
        flow=ratios.get(coin),
        whale=mkt.whales().get(coin),
        oi=mkt.oi_trend(coin, d["oi_usd"]),
        iv=mkt.iv(coin),
        news=mkt.news(coin),
        levels=build_levels(c5, c4, c1),
    )
    bs = cand.flow["ratio"] if cand.flow else None
    wl = cand.whale["ratio"] if cand.whale else None
    f8 = d["funding_8h_pct"]
    fg = fng["value"] if fng else None

    # Module 4: long triggers (default direction)
    if bs is not None and bs > 3:
        cand.long_triggers.append(f"buy/sell {bs:.2f}x")
    if wl is not None and wl > 1.5:
        cand.long_triggers.append(f"whale L/S {wl:.2f}")
    if f8 >= -0.001:
        cand.long_triggers.append("funding positive/neutral")
    if gate.btc_change >= -0.5:
        cand.long_triggers.append("BTC flat/up")
    if fg is not None and fg > 50:
        cand.long_triggers.append(f"F&G {fg}")

    # trend short (2+)
    if bs is not None and bs < 1 / 3:
        cand.trend_short.append(f"sell/buy {1 / bs:.2f}x" if bs > 0 else "sell-only flow")
    if wl is not None and wl < 0.7:
        cand.trend_short.append(f"whale L/S {wl:.2f}")
    if f8 <= cfg.funding_deep_neg_8h_pct:
        cand.trend_short.append(f"funding deeply negative {f8:.4f}%/8h")
    if gate.btc_break_support:
        cand.trend_short.append("BTC below key 4h support")
    if fg is not None and fg < 40:
        cand.trend_short.append(f"F&G {fg}")

    # reversal short (2+)
    lv = cand.levels
    for tf, candles in (("4h", c4[-2:]), ("1d", c1[-2:])):
        tops = [l for l in lv[tf] if (l.method, l.name) in {("BB", "upper"), ("FIB", "R1"), ("FIB", "R2"), ("FIB", "R3")}]
        hit = next((l for l in tops for k in candles if ind.bearish_rejection(k, l.price)), None)
        if hit:
            cand.reversal_short.append(f"bearish wick rejection at {hit.label()}")
            break
    if f8 >= cfg.funding_cap_8h_pct:
        cand.reversal_short.append(f"funding at/near cap {f8:.4f}%/8h")
    if cand.oi.get("delta_24h_pct") is not None and cand.oi["delta_24h_pct"] < -5 and d["change_pct"] > 3:
        cand.reversal_short.append(f"OI {cand.oi['delta_24h_pct']:+.1f}% on a {d['change_pct']:+.1f}% spike")
    if wl is not None and wl < 1 and d["change_pct"] > 5:
        cand.reversal_short.append(f"whale L/S {wl:.2f} after pump")
    if gate.btc_iv and gate.btc_iv.get("label") in ("HIGH", "EXTREME") and gate.btc_change > 3:
        cand.reversal_short.append(f"BTC IV {gate.btc_iv['label']} after fast move")

    if len(cand.trend_short) >= 2:
        cand.directions = ["short"]
    elif len(cand.reversal_short) >= 2:
        cand.directions = ["long", "short"]
    else:
        cand.directions = ["long"]

    # Module 3 scoring (deterministic part; Claude adds news + judgement)
    s = 0.0
    if d["change_pct"] >= -1:
        s += 1
    if wl is not None:
        s += 2 if wl >= 2 else 1 if wl >= 1.5 else 0
    if (cand.oi.get("delta_24h_pct") or 0) > 0:
        s += 1
    if cand.iv and cand.iv["label"] in ("LOW", "NORMAL"):
        s += 1
    s += len(cand.long_triggers) * 0.5
    cand.base_score = s
    if bs is not None and bs < 1 / 3:
        cand.tag = "heavy sell flow"
    return cand


# ---------------------------------------------------------------- Modules 5-7
@dataclass
class Option:
    id: str
    setup: Setup
    sizing: Sizing
    coin_ctx: dict

    def valid(self) -> bool:
        return self.sizing.ok


@dataclass
class RunParams:
    balance: float
    available: float
    mode: str
    mode_a_margin_pct: float
    mode_b_risk_pct: float
    custom_margin: float | None
    friday: bool


def build_options(cfg: Config, cand: Candidate, gate: Gate, rp: RunParams) -> tuple[list[Option], list[str]]:
    opts, rejects = [], []
    min_rr = cfg.friday_min_rr if rp.friday else cfg.min_rr
    a_margin = rp.mode_a_margin_pct
    if gate.state == "sizeup":
        a_margin = max(a_margin, cfg.mode_a_sizeup_margin_pct)
    for side in cand.directions:
        setup, why = plan_setup(
            cand.coin, side, cand.ctx["mark"], cand.levels,
            min_sl_pct=cfg.min_sl_pct, max_sl_pct=cfg.max_sl_pct, min_rr=min_rr,
            max_entry_dist_pct=cfg.max_entry_distance_pct, tol_pct=cfg.confluence_tol_pct,
        )
        if not setup:
            rejects.append(f"{cand.coin} {side}: {why}")
            continue
        sz = size_position(SizingInput(
            side=side, entry=setup.entry, sl=setup.sl, tp=setup.tp,
            balance=rp.balance, available=rp.available,
            coin_max_leverage=cand.ctx["max_leverage"], sz_decimals=cand.ctx["sz_decimals"],
            mode=rp.mode, mode_a_margin_pct=a_margin, mode_b_risk_pct=rp.mode_b_risk_pct,
            mode_b_max_margin_pct=cfg.mode_b_max_margin_pct, custom_margin=rp.custom_margin,
            margin_cap=cfg.btc_reduced_margin_usd if gate.state == "reduced" else None,
            max_leverage=cfg.max_leverage, min_leverage_mode_a=cfg.min_leverage_mode_a,
            min_sl_pct=cfg.min_sl_pct, min_rr=min_rr, maker_fee=cfg.maker_fee, taker_fee=cfg.taker_fee,
            min_order_usd=cfg.min_order_usd,
        ))
        oid = f"{cand.coin}-{side}"
        if sz.ok:
            opts.append(Option(oid, setup, sz, cand.ctx))
        else:
            rejects.append(f"{cand.coin} {side}: " + "; ".join(sz.reasons))
    return opts, rejects


def candidate_brief(c: Candidate) -> dict:
    """Compact JSON for the analyst."""
    d = c.ctx
    lv = lambda tf: [f"{l.method} {l.name} {fmt(l.price)}" for l in sorted(c.levels[tf], key=lambda x: x.price)]  # noqa: E731
    return {
        "coin": c.coin,
        "tag": c.tag,
        "price": d["mark"],
        "change_24h_pct": round(d["change_pct"], 2),
        "volume_24h_usd_m": round(d["volume_usd"] / 1e6, 1),
        "buy_sell": c.flow and {k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.flow.items()},
        "whale": c.whale and {k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.whale.items()},
        "open_interest": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.oi.items()},
        "funding_8h_equiv_pct": round(d["funding_8h_pct"], 5),
        "iv": c.iv and {k: (round(v, 1) if isinstance(v, float) else v) for k, v in c.iv.items()},
        "news": c.news,
        "levels": {tf: lv(tf) for tf in ("5m", "4h", "1d")},
        "long_triggers": c.long_triggers,
        "trend_short_triggers": c.trend_short,
        "reversal_short_triggers": c.reversal_short,
        "directions_allowed": c.directions,
        "base_score": c.base_score,
        "max_leverage": d["max_leverage"],
    }


def option_brief(o: Option) -> dict:
    s, z = o.setup, o.sizing
    return {
        "id": o.id, "coin": s.coin, "side": s.side, "entry": s.entry, "sl": s.sl, "tp": s.tp,
        "sl_pct": round(s.sl_pct, 2), "rr": round(s.rr, 2), "entry_confluence": s.entry_methods,
        "sl_anchor": s.sl_anchor, "tp_anchor": s.tp_anchor, "obstacles_before_tp": s.obstacles[:6],
        "leverage": z.leverage, "margin": round(z.margin, 2), "notional": round(z.notional, 2),
        "loss_at_sl_pct_of_account": round(z.loss_pct_of_balance, 1),
        "gain_at_tp_pct_of_account": round(z.gain_pct_of_balance, 1),
    }


def as_dict(obj) -> dict:
    return asdict(obj)
