"""HEVI's engine (protocol v3.1). Gathers live data, calls the pure rules in hlbot/rules, and runs:

  Module 12 autopilot   start ($X) -> pre-flight -> BTC gate -> sweeps -> deep dive -> filters -> rank -> card
  Module 10 execution   'y' -> re-check everything -> Module 13 final check -> post-only bracket -> verify
  Module 9 management   cancel triggers, Friday cancel/flatten, 48h time stop, early-exit suggestions
  Module 8 breakers     evaluated from the trade log every run, announced automatically
  Module 11 logging     trade log in R (MAE/MFE, fees, funding), skip log with 48h outcomes, reviews

  Autopilot (v3.1)      scans on a schedule and places trades that pass every rule, with no 'y' needed

With autopilot off, no order is placed without the user's 'y', except protocol-mandated cancels and closes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

from . import protocol as proto
from .analyst import Analyst
from .config import Config
from .levels import build_levels, fmt
from .market import Market
from .rules import breakers as brk
from .rules import bracket, metrics as mx, schedule as sch, screen
from .rules import direction as dirn
from .rules import filters as flt
from .rules import gate as gt
from .rules import review as rvw
from .rules.card import Plan, final_check, render
from .rules.checks import Check, Datum, failed, na_check, only_soft_failures, usable
from .rules.sizing import Fees, SizingInput, liq_buffer_pct, maintenance, round_price_dir, size
from .store import Store

log = logging.getLogger(__name__)

H = 3600
DAY = 86400
MS = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
EXIT_TYPES = ("tp", "sl", "time", "discretion", "early", "override", "friday", "mismatch", "liquidated")
NEWS_RULE = ("M3", "news")


@dataclass
class Eval:
    coin: str
    side: str
    tag: str
    checks: list
    plan: Plan | None
    score: int = 0
    net_rr: float = 0.0

    @property
    def ok(self) -> bool:
        return self.plan is not None and not failed(self.checks)

    def first_fail(self) -> Check | None:
        bad = failed(self.checks)
        return bad[0] if bad else None


@dataclass
class Run:
    """Everything a session's evaluation shares (Module 0 pre-flight + Module 1 gate)."""
    now: float
    equity: float
    available: float
    equity_ts: float
    stated_equity: float | None
    band: str | None
    btc: dict
    fng: int | None
    breakers: brk.BreakerState
    risk_pct: float
    half: bool
    entries_today: list
    hold: tuple
    macro_hits: list | None
    fees: Fees
    lines: list = field(default_factory=list)
    strategy: str = "swing"    # swing | day: the mode this scan used
    mode: object = None         # the Mode object for that strategy


@dataclass
class Proposal:
    id: str
    created: float
    plans: list
    text: str
    news: dict
    stated_equity: float | None
    context: dict = field(default_factory=dict)


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.P = proto.load(cfg.protocol_path) if cfg.protocol_path else proto.PROTOCOL
        # market history (OI / whale snapshots) is shared by paper and live, so going live never restarts the warm-up
        self.mkt = Market(cfg, Store(cfg.data_dir, "market.db"))
        self.analyst = Analyst(cfg)
        graduated = cfg.mode == "prove" and bool(self.mkt.store.get("graduated")) and cfg.has_wallet
        self._set_trader(cfg.mode == "live" or graduated)
        self._init_state()

    def _set_trader(self, live: bool) -> None:
        """Paper and live keep separate journals, so simulated and real results never mix."""
        self.live = live
        if live:
            from .trader import Trader
            self.store = Store(self.cfg.data_dir, "bot.db")
            self.trader = Trader(self.cfg, self.mkt)
        else:
            from .paper import PaperTrader
            self.store = Store(self.cfg.data_dir, "paper.db")
            self.trader = PaperTrader(self.cfg, self.mkt, self.store)

    def _init_state(self) -> None:
        self.pending: Proposal | None = None
        self.session: dict = {"day": None, "shown": set(), "resweeps": 0, "blocked": [], "stated": None}
        self._alerted: set[str] = set()
        self._last_pump_alert = 0.0
        self._started = time.time()
        self._last_scan = 0.0
        self._scans: dict = {"day": None, "n": 0, "last": None, "blockers": Counter(), "funnel": ""}
        self._strategy_now = "-"
        self._auto_run = False

    # ------------------------------------------------------------------ helpers
    @property
    def paper(self) -> bool:
        return bool(getattr(self.trader, "is_paper", False))

    def label(self) -> str:
        net = "" if self.cfg.is_mainnet else " (testnet)"
        if self.live:
            return "LIVE - real money" + net
        if self.cfg.mode == "prove":
            return "PROVING - paper trades on live prices until the record earns real money" + net
        return "PAPER - simulated fills on live prices" + net

    @property
    def paused(self) -> bool:
        return bool(self.mkt.store.get("paused", False))

    def set_paused(self, on: bool) -> str:
        self.mkt.store.set("paused", on)
        if on:
            return ("HEVI paused: no new trades. An open position keeps its TP/SL and is still managed "
                    "(time stop, Friday flatten). /resume to continue.")
        return "HEVI resumed: autopilot is scanning again."

    @property
    def autopilot_on(self) -> bool:
        return self.P.autopilot.enabled and not self.paused

    def calendar(self) -> proto.Calendar:
        return proto.load_calendar(self.cfg.calendar_path) if self.cfg.calendar_path else proto.load_calendar()

    def _macro_events(self) -> tuple[list[tuple[str, float]], float | None]:
        """High-impact US releases: this week's free ForexFactory feed plus calendar.toml.
        Coverage is the further of the two; no coverage = FAIL (missing data)."""
        cal = self.calendar()
        manual = [(e.name, e.ts) for e in cal.macro]
        try:
            feed, feed_cov = self.mkt.macro_calendar(tuple(self.P.schedule.macro_events))
        except Exception as e:  # noqa: BLE001
            log.warning("macro feed: %s", e)
            feed, feed_cov = [], None
        covs = [c for c in (cal.macro_covered_until, feed_cov) if c]
        return sorted(set(manual + feed), key=lambda x: x[1]), (max(covs) if covs else None)

    @property
    def user_risk_pct(self) -> float:
        return float(self.store.get("risk_pct", self.P.risk.normal_pct))

    def _since(self) -> float:
        """Paper results count from the last /paperreset."""
        return getattr(self.trader, "reset_at", 0.0) if self.paper else 0.0

    def _exits(self) -> list[brk.Exit]:
        return [brk.Exit(t["coin"], t.get("exit_type") or "unknown", t["closed_at"] or 0) for t in self.store.closed(self._since())]

    def _session(self, now: float) -> None:
        day = brk.utc_day_start(now)
        if self.session["day"] != day:
            self.session = {"day": day, "shown": set(), "resweeps": 0, "blocked": [], "stated": None}

    # ------------------------------------------------------------------ data collection (Module 3 inputs)
    def _coin(self, coin: str, now: float) -> dict:
        P, mkt = self.P, self.mkt
        stale = P.data.stale_after_min
        d = mkt.ctxs()[coin]
        cts = mkt.stamp("ctxs") or now
        x: dict = {"coin": coin, "ctx": d, "na": {}}

        def use(name, value, ts, source, zero=True):
            v, why = usable(Datum(value, source, ts), now, stale, zero)
            if why and name not in x["na"]:
                x["na"][name] = why
            x[name] = v
            return v

        price = use("price", d["mark"], cts, "Hyperliquid mark")
        use("day_change", mx.pct_change(d["mark"], mkt.day_open(coin)), cts, "UTC daily open", zero=False)
        use("volume", d["volume_usd"], cts, "24h notional")
        flow = mkt.buy_sell(coin)
        use("buy_sell", flow and flow["ratio"], flow and flow["ts"], "taker trades")
        allw = mkt.whales()
        wh = allw.get(coin)
        x["whale_absent"] = False
        if allw and not (wh and wh.get("holders", 0) >= P.deep_dive.whale_min_holders):
            # the leaderboard loaded; this coin just isn't held by enough of the top accounts. A fact about the
            # market, so it's not an "na" (missing data) entry.
            x["whale_absent"] = True
            x["whale_ls"] = None
        else:
            use("whale_ls", wh["ratio"] if wh else None, mkt.stamp("whales"), "top leaderboard positions")
        x["whale_24h"] = self.mkt.store.whale_at(coin, DAY)
        oi_then = self.mkt.store.oi_at(coin, DAY)
        use("oi_24h", mx.pct_change(d["oi_usd"], oi_then and oi_then["oi_usd"]), cts, "own hourly OI snapshots",
            zero=False)
        oi30 = self.mkt.store.oi_at(coin, 30 * DAY, tolerance_s=DAY)
        x["oi_30d"] = mx.pct_change(d["oi_usd"], oi30 and oi30["oi_usd"])
        use("funding_8h", d["funding_8h_pct"], cts, "Hyperliquid hourly funding x 8")
        try:
            x["funding_rising"] = mx.funding_rising(mkt.funding_hist(coin))
        except Exception as e:  # noqa: BLE001
            log.info("funding history %s: %s", coin, e)
            x["funding_rising"] = None

        now_ms = int(now * 1000)
        dd = P.deep_dive
        n1h = dd.rv_lookback_d * 24 + dd.rv_window_h + 30
        c5 = mkt.candles(coin, "5m", 300)
        c1h = mkt.candles(coin, "1h", n1h)
        c4 = mkt.candles(coin, "4h", 180)
        c1d = mkt.candles(coin, "1d", 90)
        if not c5 or now_ms - c5[-1]["t"] > stale * 60_000:
            x["na"]["candles"] = "5m candles missing or stale"
        h_done = mx.completed(c1h, MS["1h"], now_ms)
        x["c4_done"] = c4_done = mx.completed(c4, MS["4h"], now_ms)
        d_done = mx.completed(c1d, MS["1d"], now_ms)

        if coin in ("BTC", "ETH") and (dv := mkt.dvol(coin)):
            pct, src, ts = dv["rank"], dv["source"], mkt.stamp(f"dvol:{coin}")
        else:
            rv = mx.rv_percentile(h_done, dd.rv_window_h, dd.rv_lookback_d)
            pct, src, ts = (rv[1] if rv else None), "RV: 30-day realized-vol percentile", mkt.stamp(f"c:{coin}:1h:{n1h}")
        x["vol_pct"], x["vol_src"] = pct, src
        use("vol_label", mx.vol_label(pct, dd.vol_low_below_pct, dd.vol_high_at_or_above_pct,
                                      dd.vol_extreme_at_or_above_pct), ts or now, src, zero=False)
        x["adr"] = mx.adr_pct(d_done, dd.adr_days)
        x["atr_4h"] = mx.atr(c4_done, dd.atr_period)
        x["atr_4h_pct"] = x["atr_4h"] / price * 100 if x["atr_4h"] and price else None
        x["atr_1h_pct"] = mx.atr_pct(h_done, dd.atr_period, price) if price else None
        x["levels"] = build_levels(c5, c4, c1d)
        x["struct"] = bracket.structural(x["levels"]["4h"], P.tp)
        x["last_4h_move"] = ((c4_done[-1]["c"] / c4_done[-1]["o"] - 1) * 100) if c4_done else None
        if c4_done and d_done:
            res = [lv.price for lv in x["struct"]] + [lv.price for lv in x["levels"]["1d"] if lv.method in ("BB", "FIB", "VP")]
            g = P.gate
            x["rejection"] = any(mx.rejection_wick(c, lv, "short", g.wick_min_share, g.wick_level_tol_pct)
                                 for c in (c4_done[-1], d_done[-1]) for lv in res)
        else:
            x["rejection"] = None
        x["news_raw"] = mkt.news(coin, limit=dd.news_articles)
        x["tiers"] = d["margin_tiers"]
        x["sz_decimals"] = d["sz_decimals"]
        return x

    def _btc(self, now: float) -> dict:
        P = self.P
        x = self._coin("BTC", now)
        band = gt.btc_band(x["day_change"], P.gate)
        broke = mx.closed_below(x["c4_done"], [lv.price for lv in x["struct"]])
        dd = P.deep_dive
        lab = lambda p: mx.vol_label(p, dd.vol_low_below_pct, dd.vol_high_at_or_above_pct, dd.vol_extreme_at_or_above_pct)  # noqa: E731
        dv = self.mkt.dvol("BTC")
        vol_then = lab(dv["rank_24h_ago"]) if dv and dv.get("rank_24h_ago") is not None else None
        out = {"x": x, "band": band, "change": x["day_change"], "broke_4h": None if not x["c4_done"] else broke is not None,
               "vol_now": x["vol_label"], "vol_24h_ago": vol_then, "pump_flags": [], "pump_na": []}
        if band == "pump":
            out["pump_flags"], out["pump_na"] = gt.post_pump_flags(
                P.gate, funding_8h_pct=x["funding_8h"], funding_rising=x["funding_rising"], oi_change_24h_pct=x["oi_24h"],
                last_4h_move_pct=x["last_4h_move"], rejection_at_resistance=x["rejection"],
                whale_ls_now=x["whale_ls"], whale_ls_24h_ago=x["whale_24h"])
        return out

    # ------------------------------------------------------------------ Module 0 pre-flight + Module 1 gate
    def _run(self, now: float, stated: float | None, strategy: str | None = None) -> Run | str:
        P = self.P
        acct = self.trader.account()
        api_eq, avail = acct["account_value"], acct["withdrawable"]
        lines: list[str] = []
        if api_eq <= 0 and acct.get("spot_usdc", 0) > 0:
            return (f"Perps equity is $0 but you hold ${acct['spot_usdc']:.2f} USDC in spot. "
                    "Hyperliquid app: Portfolio -> Transfer -> Spot to Perps, then start again.")
        if api_eq <= 0:
            return "Equity is $0 - deposit USDC to Hyperliquid first."
        equity = api_eq
        if stated and abs(stated - api_eq) / api_eq > 0.05:
            equity = min(stated, api_eq)
            lines.append(f"! You said ${stated:.2f}, the API shows ${api_eq:.2f}: sizing on the lower, ${equity:.2f}.")
        self.store.record_equity(api_eq)
        if acct["positions"] or self.store.trades("open") or self.store.trades("pending"):
            return "A position or entry is already open (max 1). Check 'how is it going' or /status."

        self._cancel_stale_orders()
        today = brk.utc_day_start(now)
        entries = self.store.entries_since(today)
        btc = self._btc(now)
        band = btc["band"]
        st = self._breakers(now, api_eq, band, entries)
        half = bool(st.half_risk) or band == "half"
        user = self.user_risk_pct
        risk = min(user, P.risk.half_pct, user / 2) if half else user
        name, m = self._pick_mode(btc, strategy)
        self._strategy_now = name
        hold = sch.planned_hold(now, P.schedule, P.entry, hold_h=m.hold_h)
        events, covered = self._macro_events()
        macro, _ = sch.macro_in_window(events, covered, hold[0], hold[1], P.schedule)
        fng = self.mkt.fear_greed()
        fng_v, why = usable(Datum(fng and fng["value"], "alternative.me F&G", self.mkt.stamp("fng")), now,
                            P.data.stale_after_min, zero_is_missing=False)
        lines.insert(0, f"Equity ${equity:,.2f} (confirmed {time.strftime('%H:%M', time.gmtime(now))} UTC) | "
                        f"risk {risk:g}%{' (half)' if half else ''} | breakers: {st.status()}")
        chg = btc["change"]
        lines.insert(1, f"BTC gate: {chg:+.2f}% from the UTC open -> {gt.LABEL.get(band, 'N/A (data missing)')}"
                     if chg is not None else "BTC gate: N/A (no BTC daily open) -> FAIL")
        if btc["pump_flags"] or band == "pump":
            lines.append("Post-pump check: " + (", ".join(btc["pump_flags"]) or "no reversal flags")
                         + (f" | N/A: {', '.join(btc['pump_na'])}" if btc["pump_na"] else ""))
        lines.append(f"Fear & Greed: {fng_v}" + (f" ({fng['label']})" if fng_v is not None and fng else f" N/A ({why})"))
        return Run(now, equity, avail, now, stated, band, btc, fng_v, st, risk, half, entries, hold, macro,
                   self.trader.fees(), lines, strategy=name, mode=m)

    def _pick_mode(self, btc: dict, override: str | None = None) -> tuple[str, object]:
        """swing | day | multi. Multi uses day when BTC is moving fast or volatile, otherwise swing."""
        S = self.P.strategy
        choice = override or self.store.get("strategy", S.default)
        if choice in ("swing", "day"):
            return choice, getattr(S, choice)
        chg = btc.get("change")
        fast = (chg is not None and abs(chg) >= S.multi_day_btc_abs_move_pct) or btc.get("vol_now") in S.multi_day_vol
        return ("day", S.day) if fast else ("swing", S.swing)

    def _hold_h(self, t: dict) -> float:
        """Each trade keeps the hold of the mode it was opened in (older trades count as swing)."""
        S = self.P.strategy
        return (S.day if t.get("strategy") == "day" else S.swing).hold_h

    def strategy_label(self) -> str:
        cur = self.store.get("strategy", self.P.strategy.default)
        return cur if cur != "multi" else f"multi (now {self._strategy_now})"

    def set_strategy(self, name: str) -> str:
        opts = {
            "swing": "selective: long needs 4 of 5 signals, every protocol filter; holds up to 48h",
            "day": "quicker: 3 of the measurable long signals, no penalty for a coin no top whale holds, "
                   "thin news not blocking; holds up to 24h, TP within 1x ADR",
            "multi": "day when BTC moves 2%+ since the UTC open or volatility is HIGH, otherwise swing",
        }
        if name not in opts:
            return ("HEVI strategy (now: " + self.strategy_label() + "):\n"
                    + "\n".join(f"/mode {k} - {v}" for k, v in opts.items())
                    + "\nThe hard rules (1.5% stop, 1:3 net, 4H target, 15% risk, breakers) are the same in every mode.")
        self.store.set("strategy", name)
        return f"Strategy set to {name}: {opts[name]}. Applies from the next scan."

    def _note_scan(self, funnel: str, blockers) -> None:
        """Why a scan placed nothing. Counted for the daily report and written to the logs."""
        self._scans.setdefault("blockers", Counter()).update(blockers)
        self._scans["funnel"] = funnel
        top = ", ".join(f"{k} x{v}" for k, v in Counter(blockers).most_common(3))
        log.info("scan [%s]: %s | blocked by: %s", self._strategy_now, funnel, top or "-")

    def _breakers(self, now: float, equity: float, band: str | None, entries: list[str]) -> brk.BreakerState:
        P = self.P
        peak = max(self.store.peak_equity(self._since()), equity)
        st = brk.evaluate(
            P.breakers, exits=self._exits(), entries_today=entries, equity=equity, peak_equity=peak,
            dd_half_was_active=bool(self.store.get("dd_half_active", False)), dd_stop=self.store.get("dd_stop"),
            last_review_at=(self.store.last_event("review") or {}).get("ts"), protocol_version=P.version,
            btc_band=band, now=now)
        self.store.set("dd_half_active", st.dd_half_active)
        if st.drawdown_pct >= P.breakers.dd_stop_pct and not self.store.get("dd_stop"):
            self.store.set("dd_stop", {"since": now, "version": P.version})
            self.store.add_event("breaker", f"50% drawdown stop at equity ${equity:.2f}")
        return st

    def _hard_blocks(self, run: Run) -> list[str]:
        """Session-wide hard rules: no candidate can pass these, and no override applies."""
        P, now = self.P, run.now
        out = []
        if not sch.entry_window_open(now, P.schedule) or sch.must_be_flat(now, P.schedule):
            out.append("Module 9: no entries Friday to Sunday (entries Mon 00:00 - Thu 23:59 UTC).")
        if run.band is None:
            out.append("Module 1: BTC daily change unavailable - missing data = FAIL.")
        elif run.band == "no_trade":
            out.append("Module 1: BTC -4% or worse on the day - no new trades today.")
        out += [f"Module 8: {b}" for b in run.breakers.blocked]
        if run.macro_hits is None:
            out.append("Module 0: no macro calendar covers the planned hold (feed unreachable and calendar.toml "
                       "out of date) - missing data = FAIL.")
        return out

    def _cancel_stale_orders(self) -> None:
        """Module 0: cancel resting entry orders the bot isn't tracking (left over from earlier sessions)."""
        tracked = {int(t["entry_oid"]) for t in self.store.trades("pending") + self.store.trades("open") if t["entry_oid"]}
        for o in self.trader.open_orders():
            if not o.get("isTrigger") and int(o["oid"]) not in tracked:
                try:
                    self.trader.cancel(o["coin"], int(o["oid"]))
                    self.store.add_event("stale_cancel", f"{o['coin']} oid {o['oid']}")
                except Exception as e:  # noqa: BLE001
                    log.warning("stale cancel %s: %s", o, e)

    # ------------------------------------------------------------------ Module 2 screener
    def _screen(self, run: Run, exclude: set[str]) -> tuple[list[tuple[str, str]], list[str]]:
        P, now = self.P, run.now
        ctxs = self.mkt.ctxs()
        pool = [c for c, d in ctxs.items() if d["volume_usd"] > P.screener.sweep1_min_volume_usd]
        self.mkt.flow.ensure(pool)
        cal = self.calendar() if P.screener.unlock_check else None
        unlock_ev = [(e.coin, e.ts, e.supply_pct) for e in cal.unlocks] if cal else []
        sl_exits = [(e.coin, e.closed_at) for e in self._exits() if e.exit_type in ("sl", "liquidated")]
        rows, notes, cache = [], [], {}
        for c in pool:
            open_ = self.mkt.day_open(c)
            flow = self.mkt.buy_sell(c)
            rows.append(screen.Row(c, ctxs[c]["volume_usd"], flow and flow["ratio"], mx.pct_change(ctxs[c]["mark"], open_)))

        def skip(coin: str) -> str | None:
            if coin in exclude:
                return "shown earlier this session"
            if coin in cache:
                return cache[coin]
            fails = screen.auto_skips(
                P.screener, coin=coin,
                unlock=screen.unlock_in_window(unlock_ev, cal.unlocks_covered_until, coin, run.hold[0], run.hold[1],
                                               P.screener) if cal else None,
                vol_label=self._vol_label(coin, now),
                cooldown=screen.cooldown_until(coin, sl_exits, now, P.screener))
            if brk.reentry_blocked(coin, run.entries_today, run.band):
                fails.append(Check("M1", "no re-entries", False, "BTC half-risk band: already traded today", hard=True))
            why = "; ".join(f"{f.rule}: {f.detail}" for f in fails) if fails else None
            cache[coin] = why
            if why:
                notes.append(f"{coin}: {why}")
                self.store.add_skip(P.version, coin, None, f"M2 {fails[0].rule}", why)
            return why

        return screen.sweeps(rows, P.screener, skip), notes

    def _vol_label(self, coin: str, now: float) -> str | None:
        dd = self.P.deep_dive
        if coin in ("BTC", "ETH") and (dv := self.mkt.dvol(coin)):
            pct = dv["rank"]
        else:
            n = dd.rv_lookback_d * 24 + dd.rv_window_h + 30
            rv = mx.rv_percentile(mx.completed(self.mkt.candles(coin, "1h", n), MS["1h"], int(now * 1000)),
                                  dd.rv_window_h, dd.rv_lookback_d)
            pct = rv[1] if rv else None
        return mx.vol_label(pct, dd.vol_low_below_pct, dd.vol_high_at_or_above_pct, dd.vol_extreme_at_or_above_pct)

    # ------------------------------------------------------------------ news (Claude rates, rules decide)
    def _rate_news(self, coins: list[str], run: Run) -> dict[str, flt.News | None]:
        raw = {c: self.mkt.news(c, limit=self.P.deep_dive.news_articles) for c in coins}
        payload = {"hold_window_utc": [time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) for t in run.hold],
                   "coins": [{"coin": c, "articles": [{"id": f"{c}-{i}", **a} for i, a in enumerate(arts)]}
                             for c, arts in raw.items() if arts]}
        out: dict[str, flt.News | None] = {c: None for c in coins}
        if not payload["coins"]:
            return out
        try:
            res = self.analyst.rate_news(payload)
        except Exception as e:  # noqa: BLE001
            log.exception("news rating failed")
            run.lines.append(f"! News rating unavailable ({e}): the news filter fails (missing data).")
            return out
        for row in res.get("coins", []):
            c = row.get("coin")
            if c not in raw or not raw[c]:
                continue
            r = [x["rating"] for x in row.get("ratings", [])][: len(raw[c])]
            out[c] = flt.News(r.count("bullish"), r.count("bearish"), len(r), row.get("catalyst") or None)
        return out

    # ------------------------------------------------------------------ Modules 3-7 per coin
    def _evaluate(self, coin: str, tag: str, run: Run, news: flt.News | None, force_side: str | None = None) -> list[Eval]:
        P, now = self.P, run.now
        x = self._coin(coin, now)
        base: list[Check] = []
        for k in ("price", "candles"):
            if k in x["na"]:
                base.append(na_check("M0", "data", f"{k}: {x['na'][k]}"))
        if run.macro_hits:
            base.append(Check("M0", "macro release", False, "inside the planned hold: " + ", ".join(
                f"{n} {time.strftime('%a %H:%M', time.gmtime(t))} UTC" for n, t in run.macro_hits)))
        if x["vol_label"] == "EXTREME":
            base.append(Check("M2", "EXTREME volatility", False, "volatility >= 85th percentile", hard=True))
        if brk.reentry_blocked(coin, run.entries_today, run.band):
            base.append(Check("M1", "no re-entries", False, "BTC half-risk band: already traded today", hard=True))
        if x["funding_8h"] is None:
            base.append(na_check("M7", "funding", "needed for net R/R: " + x["na"].get("funding_8h", "missing")))

        d, m = P.direction, (run.mode or P.strategy.swing)
        lt = dirn.long_triggers(d, m, buy_sell=x["buy_sell"], whale_ls=x["whale_ls"], funding_8h=x["funding_8h"],
                                btc_band=run.band, fng=run.fng)
        tt = dirn.trend_short_triggers(d, m, buy_sell=x["buy_sell"], whale_ls=x["whale_ls"], funding_8h=x["funding_8h"],
                                       btc_broke_4h_support=run.btc["broke_4h"], fng=run.fng)
        rt = dirn.reversal_short_triggers(d, rejection_wick=x["rejection"], funding_8h=x["funding_8h"],
                                          funding_rising=x["funding_rising"], oi_change_24h=x["oi_24h"],
                                          day_change=x["day_change"], whale_ls_now=x["whale_ls"],
                                          whale_ls_24h_ago=x["whale_24h"], btc_vol_now=run.btc["vol_now"],
                                          btc_vol_24h_ago=run.btc["vol_24h_ago"], btc_change=run.btc["change"])
        sides, why = dirn.decide(d, m, lt, tt, rt)
        triggers = {"long": lt, "trend_short": tt, "reversal_short": rt}
        todo = [force_side] if force_side else (sides or ["long"])
        out = []
        for side in todo:
            checks = list(base)
            checks.append(Check("M5", "direction", side in sides, why))
            checks += flt.hard_filters(side, P.filters, P.screener, day_change_pct=x["day_change"],
                                       whale_ls=x["whale_ls"], whale_is_true=True, crowd_ls=None, whale_absent=x["whale_absent"],
                                       na_is_miss=m.na_is_miss, news_optional_below=m.news_optional_below,
                                       oi_change_24h_pct=x["oi_24h"], vol_label=x["vol_label"], news=news)
            ev = Eval(coin, side, tag, checks, None)
            self._bracket(ev, x, run, news, triggers)
            out.append(ev)
        return out

    def _bracket(self, ev: Eval, x: dict, run: Run, news: flt.News | None, triggers: dict) -> None:
        """Modules 6, 7 and 4: entry at 5m confluence, SL beyond 4H structure, TP at the first 4H level, sizing."""
        P, side, price, szd = self.P, ev.side, x["price"], x["sz_decimals"]
        if not price:
            return
        conf, why = bracket.entry_confluence(x["levels"]["5m"], price, side, P.entry)
        if not conf:
            ev.checks.append(Check("M6", "5m confluence", False, why))
            return
        up = side == "short"  # entry rounds to the passive side; SL rounds away from entry
        entry = round_price_dir(conf.price, szd, up=up)
        sl, sl_how = bracket.stop_loss(entry, side, x["struct"], x["atr_4h"], P.risk)
        tpp, tp_why = bracket.take_profit(entry, price, side, x["struct"], x["atr_4h_pct"], x["adr"], P.tp,
                                                    max_adr_mult=(run.mode or P.strategy.swing).max_adr_mult)
        if sl is None:
            ev.checks.append(Check("M7", "SL beyond 4H structure", False, sl_how, hard=True))
        if tpp is None:
            ev.checks.append(Check("M4", "TP rule", False, tp_why, hard=True))
        if sl is None or tpp is None:
            return
        sl = round_price_dir(sl, szd, up=up)
        tp = round_price_dir(tpp.tp, szd, up=up)  # rounds toward entry for longs (never past the front-run)
        z = size(SizingInput(side, entry, sl, tp, run.equity, run.available, run.risk_pct, run.half, szd, x["tiers"],
                             run.fees, x["funding_8h"] or 0.0), P.risk, P.exchange, P.filters)
        ev.checks += z.checks
        if not z.qty:
            return
        ev.score, pts = flt.rank_score(side, P.ranking, whale_ls=x["whale_ls"], oi_growth_30d_pct=x["oi_30d"],
                                       news=news, vol_label=x["vol_label"], net_rr=z.net_rr, tp_adr_mult=tpp.adr_mult)
        ev.net_rr = z.net_rr
        notes, info = [], []
        if side == "long" and (x["day_change"] or 0) < 0:
            notes.append(f"red day ({x['day_change']:+.2f}%): caution")
        if x["vol_label"] == "HIGH":
            notes.append("HIGH volatility: caution")
        if run.btc["pump_flags"]:
            notes.append("BTC post-pump flags: " + ", ".join(run.btc["pump_flags"]))
        num = lambda v, f: "N/A" if v is None else format(v, f)  # noqa: E731
        info.append(f"{x['coin']} {num(x['day_change'], '+.2f')}% today | ADR {num(x['adr'], '.2f')}% | "
                    f"4H ATR {num(x['atr_4h_pct'], '.2f')}% | 1H ATR {num(x['atr_1h_pct'], '.2f')}%")
        info.append(f"buy/sell {num(x['buy_sell'], '.2f')}x | whale L/S {num(x['whale_ls'], '.2f')} | "
                    f"OI 24h {num(x['oi_24h'], '+.1f')}% | OI 30d {num(x['oi_30d'], '+.1f')}%")
        info.append(f"funding {num(x['funding_8h'], '+.4f')}%/8h | vol {x['vol_label']} ({x['vol_src']})"
                    + (f" | news {news.bullish} bullish / {news.bearish} bearish of {news.total}" if news else ""))
        hits = [t.name for t in triggers["long" if side == "long" else "reversal_short"] if t.hit]
        if side == "short":
            hits += [t.name for t in triggers["trend_short"] if t.hit]
        info.append("triggers: " + (", ".join(hits) or "none"))
        if x["na"]:
            notes.append("N/A: " + ", ".join(f"{k} ({v})" for k, v in x["na"].items()))
        stamps = [s for s in (self.mkt.stamp("ctxs"), self.mkt.stamp("whales"), self.mkt.stamp("rss")) if s]
        ev.plan = Plan(
            coin=x["coin"], side=side, entry=entry, sl=sl, tp=tp, entry_methods=conf.methods, sl_label=sl_how,
            tp_label=tpp.level.label, tp_adr_mult=tpp.adr_mult, sizing=z, fees=run.fees, equity=run.equity,
            equity_ts=run.equity_ts, btc_band=run.band, vol_label=x["vol_label"], score=ev.score, score_points=pts,
            data_ts=min(stamps) if stamps else run.now, breakers=run.breakers.status(), notes=notes, info=info,
            price_at_card=price, tag=ev.tag, strategy=run.strategy,
            max_adr_allowed=(run.mode or P.strategy.swing).max_adr_mult,
            hold_h=(run.mode or P.strategy.swing).hold_h)

    # ------------------------------------------------------------------ Module 12 flow
    def start(self, stated_equity: float | None) -> Proposal | str:
        now = time.time()
        self._session(now)
        self.session["stated"] = stated_equity
        return self._sweep(now, stated_equity, exclude=set())

    def resweep(self) -> Proposal | str:
        """'nah' / 'skip' / 'try another': fresh candidates, max 2 per session. Standards never drop."""
        now = time.time()
        self._session(now)
        self.pending = None
        if self.session["resweeps"] >= self.P.commands.max_resweeps:
            return "No trade today. (Max re-sweeps reached - lowering standards to find a trade is not allowed.)"
        self.session["resweeps"] += 1
        return self._sweep(now, self.session["stated"], exclude=set(self.session["shown"]))

    def _sweep(self, now: float, stated: float | None, exclude: set[str]) -> Proposal | str:
        P = self.P
        if self.pending and now - self.pending.created < P.execution.card_ttl_min * 60:
            return "A card is waiting for your answer: y / nah / n."
        run = self._run(now, stated)
        if isinstance(run, str):
            return run
        hard = self._hard_blocks(run)
        if hard:
            self._note_scan("blocked before screening", Counter({hard[0][:70]: 1}))
            return "\n".join(run.lines + ["", "No trade today:"] + hard)
        cands, notes = self._screen(run, exclude)
        if not cands:
            self._note_scan("screener: no candidates", Counter({"screener": 1}))
            return "\n".join(run.lines + ["", "Screener: no coin passes the sweeps and automatic skips."] + notes[:10])
        evals = self._deep_dive(cands, run, notes)
        self.session["shown"].update(c for c, _ in cands)
        ok = sorted((e for e in evals if e.ok), key=lambda e: (e.score, e.net_rr), reverse=True)
        for e in evals:
            if not e.ok:
                f = e.first_fail()
                p = e.plan
                self.store.add_skip(P.version, e.coin, e.side, f"{f.module} {f.rule}" if f else "no setup",
                                    "; ".join(c.line() for c in failed(e.checks))[:900],
                                    p and p.entry, p and p.sl, p and p.tp)
        reasons = Counter()
        for e in evals:
            if not e.ok:
                f = e.first_fail()
                reasons[f"{f.module} {f.rule}" if f else "no complete setup"] += 1
        if not ok:
            self._note_scan(f"{len(cands)} screened, {len(evals)} checked, none passed",
                            reasons or Counter({"no candidate": 1}))
            soft = sorted((e for e in evals if e.plan and only_soft_failures(e.checks)), key=lambda e: (e.score, e.net_rr),
                          reverse=True)
            self.session["blocked"] = soft
            out = run.lines + ["", "No trade today. Candidates and the rule that blocked each:"]
            for e in evals[:8]:
                bad = failed(e.checks)
                out.append(f"- {e.coin} {e.side}: " + ("; ".join(f"{c.module} {c.rule} ({c.detail})" for c in bad[:2])
                                                       if bad else "no complete setup"))
            if soft:
                b = soft[0]
                out.append(f"\nClosest: {b.coin} {b.side}, blocked only by soft rules: "
                           + ", ".join(f"{c.module} {c.rule}" for c in failed(b.checks))
                           + ".\nTo take it anyway type:  override: [your reason]")
            out.append("Re-sweep: nah / skip / try another"
                       f" ({P.commands.max_resweeps - self.session['resweeps']} left)")
            return "\n".join(out)
        best = ok[0]
        plans = [best.plan]
        twin = next((e for e in ok if e.coin == best.coin and e.side != best.side), None)
        if twin:
            plans = sorted([best.plan, twin.plan], key=lambda p: p.side != "long")
        runner = next((e for e in ok if e.coin != best.coin), None)
        for p in plans:
            p.runner_up = f"{runner.coin} {runner.side} {runner.score}/6, 1:{runner.net_rr:.2f}" if runner else "none"
        return self._propose(plans, run, self.session.get("news") or {}, stated)

    def _deep_dive(self, cands: list[tuple[str, str]], run: Run, notes: list[str]) -> list[Eval]:
        """Two passes, so Claude only reads headlines where they can change the outcome. Pass 1 runs every rule with
        the headline count but no sentiment; pass 2 rates headlines only for coins that pass everything else."""
        counts = {c: len(self.mkt.news(c, limit=self.P.deep_dive.news_articles)) for c, _ in cands}

        def dive(coin: str, tag: str, news) -> list[Eval]:
            try:
                return self._evaluate(coin, tag, run, news)
            except Exception as e:  # noqa: BLE001
                log.exception("deep dive %s", coin)
                notes.append(f"{coin}: deep dive failed ({e})")
                return []

        def worth_news(evs: list[Eval]) -> bool:
            for e in evs:
                rest = [c for c in failed(e.checks) if (c.module, c.rule) != NEWS_RULE]
                if e.plan and (not rest or (not self.autopilot_on and not any(c.hard for c in rest))):
                    return True
            return False

        def unrated(c: str) -> flt.News:  # headlines counted, not rated yet: sentiment can't pass until rated
            return flt.News(0, 0, counts.get(c, 0), None)

        first = {c: dive(c, tag, unrated(c)) for c, tag in cands}
        want = [c for c, _ in cands if worth_news(first[c])]
        news = self._rate_news(want, run) if want else {}
        self.session["news"] = news
        out: list[Eval] = []
        for c, tag in cands:
            out += dive(c, tag, news[c] or unrated(c)) if c in news else first[c]
        return out

    def _propose(self, plans: list[Plan], run: Run, news: dict, stated: float | None) -> Proposal:
        P = self.P
        body = run.lines + [""]
        for i, p in enumerate(plans, 1):
            body.append(render(p, P, run.now, self.paper, i if len(plans) > 1 else None))
        if self._auto_run:
            body.append("HEVI autopilot: every rule passed - placing it now.")
        elif len(plans) > 1:
            body.append("Reversal signals present: both setups shown. Type  y 1  (long) or  y 2  (short).")
        else:
            body.append('Type "y" to confirm.  (nah / skip / try another = re-sweep, n = discard)')
        if not self._auto_run:
            body.append(f"Card expires in {P.execution.card_ttl_min:g} min.")
        self.pending = Proposal(uuid.uuid4().hex[:6], run.now, plans, "\n".join(body), news, stated)
        return self.pending

    def override(self, reason: str) -> Proposal | str:
        """'override: [reason]' - take the closest candidate blocked only by soft rules. Hard rules never move."""
        reason = reason.strip()
        if not reason:
            return "An override needs a reason:  override: [reason]"
        blocked = self.session.get("blocked") or []
        if not blocked:
            return "Nothing to override: no candidate from this session was blocked by soft rules only."
        e = blocked[0]
        p = e.plan
        p.override = reason
        p.overridden_rules = [f"{c.module} {c.rule}" for c in failed(e.checks)]
        self.store.add_event("override", f"{e.coin} {e.side}: {', '.join(p.overridden_rules)} - {reason}")
        now = time.time()
        run = self._run(now, self.session.get("stated"))
        if isinstance(run, str):
            return run
        hard = self._hard_blocks(run)
        if hard:
            return "Override refused - hard rules block every trade right now:\n" + "\n".join(hard)
        self.session["blocked"] = []
        return self._propose([p], run, self.session.get("news") or {}, self.session.get("stated"))

    # ------------------------------------------------------------------ Module 10 execution checklist
    def confirm(self, choice: int | None) -> str | Proposal:
        P = self.P
        prop = self.pending
        if not prop:
            return "Nothing to confirm. Send: start ($equity)"
        now = time.time()
        if now - prop.created > P.execution.card_ttl_min * 60:
            self.pending = None
            return "That card expired - prices move. Send start again."
        if len(prop.plans) > 1 and choice not in (1, 2):
            return "Two setups on this card - reply  y 1  (long) or  y 2  (short)."
        plan = prop.plans[(choice or 1) - 1]

        # 1. re-fetch and re-run every filter for this coin and side
        run = self._run(now, prop.stated_equity, strategy=plan.strategy)
        if isinstance(run, str):
            self.pending = None
            return "Card withdrawn: " + run
        hard = self._hard_blocks(run)
        if hard:
            self.pending = None
            return "Card withdrawn - a hard rule now blocks trading:\n" + "\n".join(hard)
        news = prop.news.get(plan.coin)
        ev = self._evaluate(plan.coin, plan.tag, run, news, force_side=plan.side)[0]
        bad = failed(ev.checks)
        if plan.override:
            bad = [c for c in bad if c.hard or f"{c.module} {c.rule}" not in plan.overridden_rules]
        if bad or not ev.plan:
            self.pending = None
            return "A filter changed since the card - not placing:\n" + "\n".join(c.line() for c in bad[:6])
        fresh = ev.plan
        fresh.override, fresh.overridden_rules = plan.override, plan.overridden_rules
        moved = abs(fresh.price_at_card / plan.price_at_card - 1) * 100
        z0, z1 = plan.sizing, fresh.sizing
        changed = (fresh.entry, fresh.sl, fresh.tp, z1.qty, z1.leverage) != (plan.entry, plan.sl, plan.tp, z0.qty, z0.leverage)
        if moved > P.execution.reissue_if_moved_pct or changed:
            fresh.runner_up = plan.runner_up
            newp = self._propose([fresh], run, prop.news, prop.stated_equity)
            newp.text = (f"Re-issued: price moved {moved:.2f}% since the card"
                         + (" and the numbers changed" if changed else "") + ".\n" + newp.text)
            return newp

        # Module 13 on the exact order
        acct_pos = self.trader.account()["positions"]
        problems = final_check(fresh, P, now=now, open_positions=len(acct_pos) + len(self.store.trades("open")),
                               pending_entries=len(self.store.trades("pending")), breakers_blocked=run.breakers.blocked,
                               authorized=True)
        if problems:
            self.pending = None
            self.store.add_event("hard_block", "; ".join(problems))
            return "Module 13 check failed - not placing:\n" + "\n".join(f"- {b}" for b in problems)

        # 2-3. isolated leverage, then exactly the card: ALO limit + reduce-only SL/TP (mark-price triggers)
        z = fresh.sizing
        try:
            res = self.trader.place_bracket(fresh.coin, fresh.side, z.qty, fresh.entry, fresh.sl, fresh.tp, z.leverage)
        except Exception as e:  # noqa: BLE001
            log.exception("place failed")
            self.pending = None
            return f"Order rejected by the exchange: {e}"
        self.pending = None
        oid = res.get("entry_oid")
        # 4. confirm the order is live
        status = ((self.trader.order_status(int(oid)) if oid else {}).get("order") or {}).get("status")
        if status not in ("open", "filled") and not res.get("filled_now"):
            try:
                self.trader.cancel_all(fresh.coin)
            except Exception:  # noqa: BLE001
                pass
            self.store.add_event("mismatch", f"{fresh.coin} entry status {status} after placing")
            return f"Mismatch: entry order status is {status!r} right after placing. Cancelled and reported - nothing is open."
        tid = self.store.add_trade(
            coin=fresh.coin, side=fresh.side, status="open" if res.get("filled_now") else "pending",
            entry=fresh.entry, sl=fresh.sl, tp=fresh.tp, qty=z.qty, leverage=z.leverage, margin=z.margin,
            entry_oid=oid, filled_at=now if res.get("filled_now") else None, version=P.version, score=fresh.score,
            strategy=fresh.strategy,
            btc_band=fresh.btc_band, risk_usd=z.loss_at_sl, risk_pct=z.loss_at_sl / fresh.equity * 100,
            risk_mode=z.risk_mode, override=fresh.override,
            plan={"card": prop.text, "plan": _plan_json(fresh), "equity": fresh.equity, "tp_label": fresh.tp_label,
                  "net_reward": z.net_reward, "net_rr": z.net_rr, "fees_c_pct": fresh.fees.c_pct},
        )
        return (f"{'[PAPER] ' if self.paper else ''}Placed #{tid}: {fresh.coin} {fresh.side.upper()} post-only limit "
                f"{fmt(fresh.entry)} x {z.qty:g}, order {oid} {status or 'filled'}. SL {fmt(fresh.sl)} / TP {fmt(fresh.tp)} "
                f"(mark triggers) attached, {z.leverage}x isolated.\n"
                f"Cancels if unfilled after {P.entry.cancel_after_h:g}h, on a stricter BTC band, at half-way to TP, "
                "or on a 4H close beyond the SL.")

    # ------------------------------------------------------------------ Module 9/10 live management
    def sync(self) -> list[str]:
        """Fills, partial fills, post-fill checks (TP/SL present, liq buffer, R from the actual fill), closes."""
        msgs: list[str] = []
        acct = self.trader.account()
        positions = {p["coin"]: p for p in acct["positions"]}
        orders = self.trader.open_orders()
        if acct["account_value"] > 0 and time.time() - float(self.store.get("eq_ts", 0)) > 300:
            self.store.record_equity(acct["account_value"])
            self.store.set("eq_ts", time.time())

        done: set[int] = set()
        for t in self.store.trades("pending"):
            pos = positions.get(t["coin"])
            try:
                stt = self.trader.order_status(int(t["entry_oid"])) if t["entry_oid"] else {}
            except Exception:  # noqa: BLE001
                stt = {}
            ost = (stt.get("order") or {}).get("status")
            if pos or ost == "filled":
                self.store.update_trade(t["id"], status="open", filled_at=time.time())
                t = self.store.trade(t["id"])
                msgs.append(f"#{t['id']} {t['coin']} entry filled at ~{fmt(pos['entry']) if pos else fmt(t['entry'])}"
                            + (" (partial)" if ost == "open" else "") + ".")
                if pos:
                    msgs += self._post_fill(t, pos, orders, partial=ost == "open")
                done.add(t["id"])
            elif ost in ("canceled", "rejected", "marginCanceled", "reduceOnlyCanceled") or stt.get("status") == "unknownOid":
                self.store.update_trade(t["id"], status="cancelled", closed_at=time.time(), close_reason=ost or "unknown")
                msgs.append(f"#{t['id']} {t['coin']} entry order {ost or 'gone'} - no position.")

        for t in self.store.trades("open"):
            if t["id"] in done:
                continue
            pos = positions.get(t["coin"])
            if pos:
                msgs += self._post_fill(t, pos, orders, partial=None)
                continue
            msgs += self._on_closed(t)
        return msgs

    def _post_fill(self, t: dict, pos: dict, orders: list[dict], partial: bool | None) -> list[str]:
        P, msgs = self.P, []
        coin = t["coin"]
        trig = [o for o in orders if o["coin"] == coin and o.get("isTrigger")]
        has_sl = any("Stop" in o.get("orderType", "") for o in trig)
        has_tp = any("Take Profit" in o.get("orderType", "") for o in trig)
        protected = float(self.store.get(f"protected:{t['id']}", 0) or 0)
        grew = protected and pos["size"] > protected * 1.0001
        if not (has_sl and has_tp) or grew:
            try:
                if grew:
                    for o in trig:
                        self.trader.cancel(coin, int(o["oid"]))
                self.trader.protect_position(coin, t["side"], pos["size"], t["sl"], t["tp"])
                self.store.set(f"protected:{t['id']}", pos["size"])
                msgs.append(f"#{t['id']} {coin}: TP/SL set on the position ({pos['size']:g}).")
            except Exception as e:  # noqa: BLE001
                msgs.append(f"!! #{t['id']} {coin}: TP/SL MISSING and re-attach failed: {e}. Closing (mismatch).")
                self._close(t, "mismatch")
                return msgs
        # liq buffer from the exchange's own liquidation price
        if pos.get("liq") and self.store.get(f"liqok:{t['id']}") != pos["size"]:
            buf = liq_buffer_pct(pos["liq"], t["sl"], pos["entry"], t["side"])
            if buf + 1e-9 < P.risk.liq_beyond_sl_pct:
                add = self._margin_to_restore(t, pos)
                try:
                    self.trader.add_margin(coin, add)
                    msgs.append(f"#{t['id']} {coin}: liq was only {buf:.2f}% beyond the SL - added ${add:.2f} margin.")
                except Exception as e:  # noqa: BLE001
                    msgs.append(f"!! #{t['id']} {coin}: liq {buf:.2f}% beyond SL and adding margin failed ({e}). "
                                "Closing (mismatch).")
                    self._close(t, "mismatch")
                    return msgs
            self.store.set(f"liqok:{t['id']}", pos["size"])
        # recompute R from the actual fill
        plan = json.loads(t["plan"] or "{}")
        c = plan.get("fees_c_pct", P.fees.maker_pct + P.fees.taker_pct + P.fees.slippage_pct) / 100
        risk = pos["size"] * abs(pos["entry"] - t["sl"]) + pos["size"] * pos["entry"] * c
        if t.get("fill_px") != pos["entry"] or abs((t.get("risk_usd") or 0) - risk) > 1e-9:
            self.store.update_trade(t["id"], fill_px=pos["entry"], risk_usd=risk, qty=pos["size"])
        return msgs

    def _margin_to_restore(self, t: dict, pos: dict) -> float:
        P = self.P
        side, entry, qty = t["side"], pos["entry"], pos["size"]
        s = 1 if side == "long" else -1
        tiers = self.mkt.ctxs()[t["coin"]]["margin_tiers"]
        mm, _, mmr = maintenance(entry * qty, tiers)
        target = t["sl"] - s * (P.risk.liq_beyond_sl_pct + 0.1) / 100 * max(entry, t["sl"])
        need = s * (entry - target) * qty * (1 - mmr * s) + mm
        return max(0.01, need - float(pos.get("margin_used") or 0))

    def _close(self, t: dict, why: str) -> None:
        try:
            self.store.update_trade(t["id"], close_reason=why)
            self.trader.close(t["coin"])
        except Exception as e:  # noqa: BLE001
            log.warning("close %s: %s", t["coin"], e)

    def _on_closed(self, t: dict) -> list[str]:
        P = self.P
        fills = [f for f in self.trader.fills_since(t["created_at"] - 5) if f["coin"] == t["coin"]]
        closing = [f for f in fills if "Close" in f.get("dir", "")]
        fees = sum(float(f.get("fee", 0)) for f in fills)
        gross = sum(float(f.get("closedPnl", 0)) for f in closing)
        since = t["filled_at"] or t["created_at"]
        try:
            funding = self.trader.funding_paid(t["coin"], since)
        except Exception:  # noqa: BLE001
            funding = 0.0
        qty = sum(float(f["sz"]) for f in closing)
        exit_px = sum(float(f["px"]) * float(f["sz"]) for f in closing) / qty if qty else None
        self.trader.cancel_all(t["coin"])  # leftover TP or SL
        result = gross - fees - funding
        exit_type = t.get("close_reason") if t.get("close_reason") in EXIT_TYPES else None
        if any(f.get("liquidation") for f in closing):
            exit_type = "liquidated"
        if exit_px and not exit_type:
            if abs(exit_px - t["tp"]) / t["tp"] < 0.004:
                exit_type = "tp"
            elif abs(exit_px - t["sl"]) / t["sl"] < 0.004:
                exit_type = "sl"
            else:
                exit_type = "discretion"
        exit_type = exit_type or "unknown"
        risk = t.get("risk_usd") or 0
        r = result / risk if risk else 0.0
        mae, mfe = self._excursions(t, since, time.time())
        self.store.update_trade(t["id"], status="closed", closed_at=time.time(), exit_px=exit_px, pnl=result,
                                close_reason=exit_type, exit_type=exit_type, result_r=r, mae_r=mae, mfe_r=mfe,
                                fees=fees, funding=funding)
        msg = [f"#{t['id']} {t['coin']} {t['side'].upper()} CLOSED ({exit_type}) at {fmt(exit_px) if exit_px else '?'} "
               f"-> ${result:+.2f} = {r:+.2f}R (fees ${fees:.2f}, funding ${funding:+.2f})"]
        try:
            msg.append(self.study(t["id"]))
        except Exception as e:  # noqa: BLE001
            log.exception("study failed")
            msg.append(f"(post-trade study failed: {e})")
        n = len(self.store.closed(self._since()))
        if n and n % P.review.every_n_trades == 0:
            msg.append(f"\n{n} trades closed: {P.review.every_n_trades}-trade review\n" + self.review())
        return ["\n".join(msg)]

    def _excursions(self, t: dict, start: float, end: float) -> tuple[float | None, float | None]:
        """MAE / MFE in R from 5m candles while the position was open."""
        risk, fill = t.get("risk_usd"), t.get("fill_px") or t["entry"]
        if not risk:
            return None, None
        try:
            c = [x for x in self.mkt.candles_range(t["coin"], "5m", int(start * 1000) - 300_000, int(end * 1000))
                 if x["t"] + 300_000 > start * 1000]
        except Exception:  # noqa: BLE001
            return None, None
        if not c:
            return None, None
        hi, lo = max(x["h"] for x in c), min(x["l"] for x in c)
        qty = t["qty"]
        if t["side"] == "long":
            return min(0.0, (lo - fill) * qty / risk), max(0.0, (hi - fill) * qty / risk)
        return min(0.0, (fill - hi) * qty / risk), max(0.0, (fill - lo) * qty / risk)

    def manage(self) -> list[str]:
        """Cancel triggers for unfilled entries, Friday cancel/flatten, 48h time stop, early-exit suggestions,
        breaker announcements. Only protocol-mandated cancels and closes happen without the user."""
        P, now, msgs = self.P, time.time(), []
        s = P.schedule
        pend = self.store.trades("pending")
        opens = self.store.trades("open")
        if not pend and not opens:
            return msgs
        btc_band = None
        try:
            btc_band = gt.btc_band(mx.pct_change(self.mkt.ctxs()["BTC"]["mark"], self.mkt.day_open("BTC")), P.gate)
        except Exception:  # noqa: BLE001
            pass
        partial = []
        for o in opens:  # a partially filled entry still has a resting remainder to manage
            if not o["entry_oid"] or self.store.get(f"rest_done:{o['id']}"):
                continue
            try:
                ost = (self.trader.order_status(int(o["entry_oid"])).get("order") or {}).get("status")
            except Exception:  # noqa: BLE001
                ost = None
            if ost == "open":
                partial.append(o)
            elif ost is not None:
                self.store.set(f"rest_done:{o['id']}", True)  # fully filled or gone: nothing left to cancel
        for t in pend + partial:
            why = self._cancel_reasons(t, now, btc_band)
            if not why:
                continue
            try:
                self.trader.cancel(t["coin"], int(t["entry_oid"]))
            except Exception as e:  # noqa: BLE001
                log.info("cancel %s: %s", t["coin"], e)
            if t["status"] == "pending":
                self.store.update_trade(t["id"], status="cancelled", closed_at=now, close_reason="; ".join(why))
                msgs.append(f"#{t['id']} {t['coin']} entry cancelled: {'; '.join(why)}.")
            else:
                self.store.set(f"rest_done:{t['id']}", True)
                msgs.append(f"#{t['id']} {t['coin']}: unfilled remainder cancelled ({'; '.join(why)}). Filled part keeps TP/SL.")
        for t in opens:
            why = None
            if sch.must_be_flat(now, s):
                why = "friday"
            elif t["filled_at"] and now >= sch.time_stop_at(t["filled_at"], s, self._hold_h(t)):
                why = "time"
            if why:
                self._close(t, why)
                msgs.append(f"#{t['id']} {t['coin']}: closing at market ({'Friday 20:00 UTC flatten' if why == 'friday' else '48h time stop'}).")
                continue
            msgs += self._early_exit_alerts(t, now)
        return msgs

    def _cancel_reasons(self, t: dict, now: float, btc_band: str | None) -> list[str]:
        P = self.P
        if sch.must_cancel_entries(now, P.schedule):
            return ["Friday 00:00 UTC: unfilled entries cancel"]
        try:
            price = self.mkt.ctxs()[t["coin"]]["mark"]
            c4 = mx.completed(self.mkt.candles(t["coin"], "4h", 3), MS["4h"], int(now * 1000))
        except Exception:  # noqa: BLE001
            return []
        band0 = t.get("btc_band") or "normal"
        return sch.cancel_triggers(P.entry, side=t["side"], created_at=t["created_at"], now=now, entry=t["entry"],
                                   sl=t["sl"], tp=t["tp"], price=price, band_at_order=band0, band_now=btc_band,
                                   last_4h_close=c4[-1]["c"] if c4 else None,
                                   stricter=bool(btc_band and gt.is_stricter(btc_band, band0)))

    def _early_exit_alerts(self, t: dict, now: float) -> list[str]:
        P = self.P
        try:
            btc = mx.pct_change(self.mkt.ctxs()["BTC"]["mark"], self.mkt.day_open("BTC"))
        except Exception:  # noqa: BLE001
            btc = None
        events, _ = self._macro_events()
        end = sch.time_stop_at(t["filled_at"] or now, P.schedule, self._hold_h(t))
        ahead = [(n, ts) for n, ts in events if now <= ts <= end and n in P.schedule.macro_events]
        out = []
        for s in sch.early_exit_suggestions(t["side"], btc, ahead, P.schedule):
            key = f"{t['id']}:{s.split(' ')[0]}:{s[-14:]}"
            if key not in self._alerted:
                self._alerted.add(key)
                out.append(self._early_exit(t, s))
        return out

    def _early_exit(self, t: dict, why: str) -> str:
        """Objective early-exit trigger: autopilot acts on it (exit type 'early', later compared with what the
        bracket would have made); with autopilot off it is only a suggestion."""
        if self.autopilot_on and self.P.autopilot.act_on_early_exits:
            self.store.add_event("early_exit", f"#{t['id']} {t['coin']}: {why}")
            self._close(t, "early")
            return f"HEVI closed #{t['id']} {t['coin']} early: {why}. Result follows."
        return (f"#{t['id']} {t['coin']} early-exit suggestion: {why}. Your call: /close {t['coin']} to exit "
                "(logged as discretion), or hold the bracket.")

    def pump_watch(self) -> str | None:
        P = self.P
        try:
            btc = self._btc(time.time())
        except Exception:  # noqa: BLE001
            return None
        if btc["band"] != "pump" or time.time() - self._last_pump_alert < 4 * H:
            return None
        self._last_pump_alert = time.time()
        flags = btc["pump_flags"]
        head = "REVERSAL RISK FLAGGED" if gt.reversal_risk(flags, P.gate) else "no reversal flag yet"
        return (f"BTC {btc['change']:+.2f}% from the UTC open - post-pump check: {head}\n"
                + ("; ".join(flags) or "no flags") + (f"\nN/A: {', '.join(btc['pump_na'])}" if btc["pump_na"] else ""))

    def hourly(self) -> list[str]:
        """Snapshots (OI, whale L/S), 48h skip outcomes, discretion counterfactuals, thesis-breaking news check."""
        msgs: list[str] = []
        self.mkt.record_oi()
        P = self.P
        wh = self.mkt.whales()
        self.mkt.store.add_whales([(c, w["ratio"]) for c, w in wh.items()
                                   if w.get("ratio") is not None and w.get("holders", 0) >= P.deep_dive.whale_min_holders])
        self.mkt.flow.ensure([c for c, d in self.mkt.ctxs().items() if d["volume_usd"] > P.screener.sweep1_min_volume_usd])
        for sk in self.store.skips_due(time.time() - P.review.skip_check_after_h * H):
            self.store.set_skip_outcome(sk["id"], self._first_touch(sk["coin"], sk["side"], sk["entry"], sk["sl"], sk["tp"],
                                                                    sk["ts"], sk["ts"] + P.review.skip_check_after_h * H))
        for t in self.store.closed(self._since()):
            if t.get("exit_type") in ("discretion", "early") and t.get("bracket_r") is None and t["filled_at"]:
                end = sch.time_stop_at(t["filled_at"], P.schedule, self._hold_h(t))
                if time.time() >= end:
                    self.store.update_trade(t["id"], bracket_r=self._bracket_r(t, end))
        for t in self.store.trades("open"):
            msgs += self._thesis_check(t)
        return msgs

    def _first_touch(self, coin, side, entry, sl, tp, start, end, need_fill=True) -> str:
        try:
            c = self.mkt.candles_range(coin, "5m", int(start * 1000), int(end * 1000))
        except Exception:  # noqa: BLE001
            return "n/a"
        s = 1 if side == "long" else -1
        filled = not need_fill
        fill_deadline = start + self.P.entry.cancel_after_h * H
        for k in c:
            if not filled:
                if k["t"] / 1000 > fill_deadline:
                    return "no fill"
                filled = (k["l"] <= entry) if s == 1 else (k["h"] >= entry)
                if not filled:
                    continue
            hit_sl = k["l"] <= sl if s == 1 else k["h"] >= sl
            hit_tp = k["h"] >= tp if s == 1 else k["l"] <= tp
            if hit_sl:
                return "sl"  # a candle touching both counts as SL (conservative)
            if hit_tp:
                return "tp"
        return "neither" if filled else "no fill"

    def _bracket_r(self, t: dict, end: float) -> float | None:
        out = self._first_touch(t["coin"], t["side"], t["entry"], t["sl"], t["tp"], t["filled_at"], end, need_fill=False)
        plan = json.loads(t["plan"] or "{}")
        if out == "tp":
            return float(plan.get("net_rr", 3.0))
        if out == "sl":
            return -1.0
        try:
            c = self.mkt.candles_range(t["coin"], "5m", int(end * 1000) - 600_000, int(end * 1000))
            px = c[-1]["c"]
        except Exception:  # noqa: BLE001
            return None
        s = 1 if t["side"] == "long" else -1
        return s * (px - (t.get("fill_px") or t["entry"])) * t["qty"] / t["risk_usd"] if t.get("risk_usd") else None

    def _thesis_check(self, t: dict) -> list[str]:
        arts = [a for a in self.mkt.news(t["coin"], limit=5) if a["age_h"] * H <= time.time() - (t["filled_at"] or t["created_at"])]
        key = f"thesis:{t['id']}"
        seen = set(self.store.get(key, []))
        new = [a for a in arts if a["title"] not in seen]
        if not new:
            return []
        self.store.set(key, sorted(seen | {a["title"] for a in new}))
        try:
            r = self.analyst.thesis_check({"coin": t["coin"], "side": t["side"], "articles": new})
        except Exception:  # noqa: BLE001
            return []
        if r.get("thesis_breaking"):
            return [self._early_exit(t, f"thesis-breaking news - {r['reason']}")]
        return []

    # ------------------------------------------------------------------ Module 11 study + review
    def study(self, tid: int) -> str:
        t = self.store.trade(tid)
        hyps = [{"id": h["id"], "text": h["text"], "support": len(h["support"])} for h in self.store.lessons("hypothesis")]
        ctx = {
            "trade": {k: t.get(k) for k in ("coin", "side", "entry", "fill_px", "sl", "tp", "qty", "leverage", "exit_px",
                                            "exit_type", "pnl", "result_r", "mae_r", "mfe_r", "fees", "funding", "score",
                                            "btc_band", "override")},
            "held_hours": round(((t["closed_at"] or time.time()) - (t["filled_at"] or t["created_at"])) / 3600, 1),
            "plan": json.loads(t["plan"] or "{}").get("plan"),
            "rules": self.store.active_lessons(),
            "hypotheses": hyps,
        }
        r = self.analyst.study(ctx)
        self.store.update_trade(tid, study=r)
        out = [f"Study: {r['what_happened']}"]
        if r["went_right"]:
            out.append("Right: " + "; ".join(r["went_right"]))
        if r["went_wrong"]:
            out.append("Wrong: " + "; ".join(r["went_wrong"]))
        out.append(f"Lesson (hypothesis): {r['lesson']}")
        valid = {h["id"] for h in hyps}
        for lid in r.get("supports", []):
            if lid in valid:
                self.store.support_lesson(lid, tid)
                out.append(f"Supports hypothesis #{lid}.")
        if r.get("new_hypothesis", "").strip():
            lid = self.store.add_lesson(r["new_hypothesis"].strip(), tid)
            out.append(f"New hypothesis #{lid}: {r['new_hypothesis'].strip()} (rule changes wait for 5+ supporting "
                       f"trades and a {self.P.review.every_n_trades}-trade review)")
        return "\n".join(out)

    def logged(self) -> list[rvw.LoggedTrade]:
        return [rvw.LoggedTrade(t["pnl"] or 0.0, t["result_r"] or 0.0, t.get("exit_type") or "unknown",
                                bool(t.get("override")), t.get("mae_r"), t.get("mfe_r"), t.get("bracket_r"))
                for t in self.store.closed(self._since()) if t.get("result_r") is not None]

    def review(self) -> str:
        P = self.P
        self.store.add_event("review", "review shown")
        s = rvw.summarize(self.logged(), P.review, P.risk)
        label = ("PAPER review (since reset)" if self.paper else "LIVE review") + f" - protocol v{P.version}"
        text = rvw.format_review(s, label, P.review)
        by: dict[str, list[float]] = {}
        for t in self.store.closed(self._since()):
            by.setdefault(t.get("strategy") or "swing", []).append(t.get("result_r") or 0.0)
        if by:
            text += "\nBy strategy: " + " | ".join(f"{k} {len(v)} trades, {sum(v):+.2f}R" for k, v in sorted(by.items()))
        skips = [k for k in self.store.skips(500) if k["outcome"] in ("tp", "sl", "no fill", "neither")]
        if skips:
            n = {o: sum(1 for k in skips if k["outcome"] == o) for o in ("tp", "sl", "no fill", "neither")}
            text += (f"\nSkips checked 48h later: {len(skips)} - would have hit TP first {n['tp']}, SL first {n['sl']}, "
                     f"never filled {n['no fill']}, neither {n['neither']}")
        if rvw.discretion_restricted(self.logged(), P.review):
            text += "\nDiscretionary exits are underperforming the bracket: /close now needs a reason."
        return text

    def discretion_restricted(self) -> bool:
        return rvw.discretion_restricted(self.logged(), self.P.review)

    # ------------------------------------------------------------------ autopilot (v3.1)
    def autopilot_tick(self) -> list[str]:
        """Called every minute. Daily report, go-live gate, self-review after a breaker pause, and - when flat,
        inside the entry window and due - a full scan whose winning setup is placed without waiting for 'y'."""
        P, A, now = self.P, self.P.autopilot, time.time()
        msgs: list[str] = []
        if not A.enabled:
            return msgs
        day = brk.utc_day_start(now)
        if self._scans["day"] != day:
            self._scans = {"day": day, "n": 0, "last": None, "blockers": Counter(), "funnel": ""}
        if time.gmtime(now).tm_hour >= A.report_hour_utc and self.mkt.store.get("report_day") != day:
            self.mkt.store.set("report_day", day)
            msgs.append(self.daily_report())
        msgs += self._maybe_graduate(now)
        if self.paused or self.pending:
            return msgs
        if not sch.entry_window_open(now, P.schedule) or sch.must_be_flat(now, P.schedule):
            return msgs
        if self.store.trades("open") or self.store.trades("pending"):
            return msgs
        if now - self._started < A.warmup_min * 60 or now - self._last_scan < A.scan_every_min * 60:
            return msgs
        self._last_scan = now
        self._scans["n"] += 1
        msgs += self._self_review(now)
        self._auto_run = True
        try:
            res = self.start(None)
            if not isinstance(res, Proposal):
                self._scans["last"] = _first_reason(res)
                return msgs
            card, out = res.text, None
            for _ in range(2):  # a re-issued card (price moved > 0.3%) gets one more full check
                plans = self.pending.plans
                out = self.confirm(None if len(plans) == 1 else _pick(plans))
                if not isinstance(out, Proposal):
                    break
                card = out.text
            if isinstance(out, Proposal):
                self.pending = None
                self._scans["last"] = "price kept moving while placing; next scan will re-check"
                return msgs
            self._scans["last"] = out.splitlines()[0]
            if "Placed #" in out:
                msgs.append(card + "\n\n" + out)
            elif any(k in out for k in ("rejected", "Mismatch", "Module 13")):
                msgs.append("HEVI autopilot could not place a setup:\n" + out)  # problems are worth a message
            return msgs
        finally:
            self._auto_run = False
            self.pending = None

    def _self_review(self, now: float) -> list[str]:
        """A breaker that waits for a review (3 straight SLs, after the 48h pause): HEVI runs the review itself.
        The 50% drawdown stop is different: it also needs a new protocol version, so it stays stopped."""
        try:
            eq = self.trader.account()["account_value"]
        except Exception:  # noqa: BLE001
            return []
        st = self._breakers(now, eq, None, self.store.entries_since(brk.utc_day_start(now)))
        if any("send 'review'" in b for b in st.blocked):
            return ["HEVI self-review (required after the circuit-breaker pause):\n" + self.review()]
        return []

    def _maybe_graduate(self, now: float) -> list[str]:
        """MODE=prove: switch from paper to real money once the paper record passes the go-live gate."""
        A = self.P.autopilot
        if self.cfg.mode != "prove" or self.live:
            return []
        ok, why = rvw.go_live_ready(self.logged(), A)
        if not ok:
            return []
        day = brk.utc_day_start(now)
        if not self.cfg.has_wallet:
            if self.mkt.store.get("graduate_nag") == day:
                return []
            self.mkt.store.set("graduate_nag", day)
            return [f"HEVI's paper record passed the go-live gate ({why}). Add HL_ACCOUNT_ADDRESS and "
                    "HL_API_PRIVATE_KEY to switch to real money automatically."]
        if self.store.trades("open") or self.store.trades("pending"):
            return []  # wait until the paper book is flat
        self.mkt.store.set("graduated", {"at": now, "why": why, "version": self.P.version})
        self._set_trader(True)
        self.store.add_event("go_live", why)
        return [f"HEVI is going LIVE. Paper record: {why}. The next setup that passes every rule uses real money, "
                f"sized at {self.user_risk_pct:g}% risk per trade."]

    def go_live_status(self) -> str:
        if self.live:
            return "live"
        if self.cfg.mode == "paper":
            return "paper only (MODE=paper)"
        return "go-live gate: " + rvw.go_live_ready(self.logged(), self.P.autopilot)[1]

    def daily_report(self) -> str:
        now = time.time()
        day = brk.utc_day_start(now)
        try:
            acct = self.trader.account()
        except Exception as e:  # noqa: BLE001
            return f"HEVI daily report: account unavailable ({e})"
        eq = acct["account_value"]
        peak = max(self.store.peak_equity(self._since()), eq)
        st = self._breakers(now, eq, None, self.store.entries_since(day))
        today = [t for t in self.store.closed(max(day, self._since()))]
        r = sum(t.get("result_r") or 0 for t in today)
        usd = sum(t.get("pnl") or 0 for t in today)
        out = [f"HEVI daily report - {time.strftime('%a %d %b', time.gmtime(now))} (UTC)",
               f"Mode: {self.label()} | {self.go_live_status()}",
               f"Equity ${eq:,.2f} | peak ${peak:,.2f} | drawdown {st.drawdown_pct:.1f}%",
               f"Closed today: {len(today)} trade(s), ${usd:+.2f} ({r:+.2f}R)"]
        for p in acct["positions"]:
            out.append(f"Open: {p['coin']} {p['side']} {p['size']:g} @ {fmt(p['entry'])}, uPnL ${p['upnl']:+.2f}")
        out.append(f"Scans today: {self._scans['n']}" + (f" | last: {self._scans['last']}" if self._scans["last"] else ""))
        out.append(f"Strategy: {self.strategy_label()}")
        bl = self._scans.get("blockers") or Counter()
        if bl:
            out.append("Why no trade (most common): " + ", ".join(f"{k} x{v}" for k, v in bl.most_common(4)))
        if self._scans.get("funnel"):
            out.append(f"Last scan: {self._scans['funnel']}")
        out.append(f"Breakers: {st.status()}" + (" | autopilot PAUSED" if self.paused else ""))
        return "\n".join(out)

    # ------------------------------------------------------------------ commands
    def set_risk(self, pct: float) -> str:
        P = self.P
        if not (0 < pct <= P.risk.ceiling_pct):
            return f"Risk must be above 0 and at most the {P.risk.ceiling_pct:g}% ceiling."
        self.store.set("risk_pct", pct)
        return f"Risk set to {pct:g}% of equity at the SL (half-risk states use {min(P.risk.half_pct, pct / 2):g}%)."

    def how_is_it_going(self) -> str:
        acct = self.trader.account()
        out = []
        for t in self.store.trades("open"):
            pos = next((p for p in acct["positions"] if p["coin"] == t["coin"]), None)
            if not pos:
                continue
            px = self.mkt.ctxs()[t["coin"]]["mark"]
            risk = t.get("risk_usd") or 0
            try:
                funding = self.trader.funding_paid(t["coin"], t["filled_at"] or t["created_at"])
            except Exception:  # noqa: BLE001
                funding = 0.0
            left = sch.time_stop_at(t["filled_at"] or time.time(), self.P.schedule, self._hold_h(t)) - time.time()
            out.append(f"#{t['id']} {t['coin']} {t['side']} {pos['size']:g} @ {fmt(pos['entry'])} | now {fmt(px)}\n"
                       f"uPnL ${pos['upnl']:+.2f}" + (f" = {pos['upnl'] / risk:+.2f}R" if risk else "")
                       + f" | to TP {abs(t['tp'] / px - 1) * 100:.2f}% | to SL {abs(t['sl'] / px - 1) * 100:.2f}%\n"
                       f"Funding paid ${funding:+.2f} | time stop in {max(0, left) / 3600:.1f}h")
        for t in self.store.trades("pending"):
            px = self.mkt.ctxs()[t["coin"]]["mark"]
            out.append(f"#{t['id']} {t['coin']} {t['side']} entry {fmt(t['entry'])} resting, price {fmt(px)} "
                       f"({(px / t['entry'] - 1) * 100:+.2f}% away), placed {(time.time() - t['created_at']) / 3600:.1f}h ago")
        return "\n\n".join(out) or "No open position or resting entry."

    def status(self) -> str:
        acct = self.trader.account()
        st = self._breakers(time.time(), acct["account_value"], None, self.store.entries_since(brk.utc_day_start(time.time())))
        peak = max(self.store.peak_equity(self._since()), acct["account_value"])
        out = [f"HEVI - {self.label()} | protocol v{self.P.version} | strategy {self.strategy_label()} | autopilot "
               + ("PAUSED" if self.paused else "on" if self.P.autopilot.enabled else "off"),
               f"Equity ${acct['account_value']:.2f} | available ${acct['withdrawable']:.2f} | peak ${peak:.2f} "
               f"(drawdown {st.drawdown_pct:.1f}%)" + (f" | spot USDC ${acct['spot_usdc']:.2f}" if acct.get("spot_usdc") else ""),
               f"Risk {self.user_risk_pct:g}% | breakers: {st.status()}"]
        for p in acct["positions"]:
            out.append(f"{p['coin']} {p['side']} {p['size']:g} @ {fmt(p['entry'])} | uPnL ${p['upnl']:+.2f} | liq {p['liq']}")
        for t in self.store.trades("pending"):
            out.append(f"#{t['id']} pending {t['coin']} {t['side']} limit {fmt(t['entry'])}")
        if len(out) == 3:
            out.append("No positions or pending entries.")
        out += self._schedule_lines(time.time())
        return "\n".join(out)

    def _schedule_lines(self, now: float) -> list[str]:
        """When the next scan happens and what HEVI is still waiting for - so 'why no trades?' answers itself."""
        P, A = self.P, self.P.autopilot
        hist = self.mkt.store.oi_history_hours()
        lines = [self.go_live_status().capitalize()]
        if hist < 24:
            ready = now + (24 - hist) * 3600
            lines.append(f"Market history: {hist:.0f}h of the 24h needed - no coin can pass the OI filter before "
                         f"{time.strftime('%a %H:%M', time.gmtime(ready))} UTC (needs the phone online)")
        if not A.enabled:
            return lines + ["Autopilot off - trade manually with start ($X)"]
        if self.paused:
            return lines + ["Autopilot paused - /resume to scan again"]
        if self.store.trades("open") or self.store.trades("pending"):
            return lines + ["Next scan: after the current trade closes (max 1 at a time)"]
        warm = self._started + A.warmup_min * 60
        if sch.entry_window_open(now, P.schedule) and not sch.must_be_flat(now, P.schedule):
            nxt = max(now, warm, self._last_scan + A.scan_every_min * 60)
            why = f"every {A.scan_every_min:g} min"
        else:
            d = time.gmtime(now)
            days = (7 - d.tm_wday) % 7 or 7  # next Monday
            nxt = max(brk.utc_day_start(now) + days * 86400, warm)
            why = "entries are Mon-Thu UTC; none on Fri-Sun"
        lines.append(f"Next scan: {time.strftime('%a %d %b %H:%M', time.gmtime(nxt))} UTC ({why})")
        return lines

    def journal(self, n: int = 10) -> str:
        rows = [t for t in self.store.trades(limit=50) if t["status"] in ("closed", "open", "pending")][:n]
        if not rows:
            return "Journal is empty."
        out = []
        for t in rows:
            r = f"{t['result_r']:+.2f}R ${t['pnl']:+.2f}" if t.get("result_r") is not None else ""
            out.append(f"#{t['id']} v{t.get('version') or '1'} {t['coin']} {t['side']} {t['status']} "
                       f"{t.get('exit_type') or ''} {r}" + (" [override]" if t.get("override") else ""))
        return "\n".join(out)

    def close_manual(self, coin: str, reason: str | None) -> str:
        if self.discretion_restricted() and not reason:
            return ("Discretionary exits have underperformed the bracket over the last 10 - add a reason: "
                    f"/close {coin} <reason>")
        for t in self.store.trades("open"):
            if t["coin"] == coin:
                self.store.update_trade(t["id"], close_reason="discretion")
                if reason:
                    self.store.add_event("discretion", f"#{t['id']} {coin}: {reason}")
        self.trader.close(coin)
        return f"{coin} market close sent (logged as a discretionary exit). Result, R and study will follow."


def _pick(plans: list[Plan]) -> int:
    """Two setups on one coin (reversal signals): the higher score, then net R/R; long is the default on a tie."""
    best = max(range(len(plans)), key=lambda i: (plans[i].score, plans[i].sizing.net_rr, plans[i].side == "long"))
    return best + 1


def _first_reason(text: str) -> str:
    """One line for the daily report: why the last scan placed nothing."""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if ln.startswith(("No trade today", "Screener:")):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            return (ln + " " + nxt).strip()[:200]
    return (lines[-1] if lines else "no result")[:200]


def _plan_json(p: Plan) -> dict:
    z = p.sizing
    return {"strategy": p.strategy, "coin": p.coin, "side": p.side, "entry": p.entry, "sl": p.sl, "tp": p.tp, "sl_label": p.sl_label,
            "tp_label": p.tp_label, "entry_methods": list(p.entry_methods), "score": p.score,
            "score_points": p.score_points, "qty": z.qty, "notional": z.notional, "leverage": z.leverage,
            "mmr": z.mmr, "margin": z.margin, "liq": z.liq, "liq_buffer_pct": z.liq_buffer_pct, "risk_mode": z.risk_mode,
            "risk_pct": z.risk_pct, "loss_at_sl": z.loss_at_sl, "net_reward": z.net_reward, "net_rr": z.net_rr,
            "funding_cost": z.funding_cost, "btc_band": p.btc_band, "vol": p.vol_label, "notes": p.notes,
            "override": p.override, "overridden": p.overridden_rules}
