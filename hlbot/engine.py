"""Ties the modules together: autopilot flow (Module 10), trade management (Module 8), post-trade study (Module 9)."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from . import strategy as st
from .analyst import Analyst
from .config import Config
from .levels import build_levels, fmt, nearest
from .market import Market
from .store import Store
from .trader import Trader

log = logging.getLogger(__name__)


@dataclass
class Proposal:
    id: str
    created: float
    options: list[st.Option]
    text: str
    context: dict = field(default_factory=dict)


def pct(a: float, b: float) -> float:
    return (a / b - 1) * 100


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.data_dir)
        self.mkt = Market(cfg, self.store)
        self.trader = Trader(cfg, self.mkt)
        self.analyst = Analyst(cfg)
        self.pending: Proposal | None = None
        self._last_pump_alert = 0.0
        self._friday_alarm_day: str | None = None

    # ----------------------------------------------------------- settings
    @property
    def mode(self) -> str:
        return self.store.get("mode", self.cfg.default_mode)

    @property
    def risk_pct(self) -> float:
        return float(self.store.get("mode_b_risk_pct", self.cfg.mode_b_risk_pct))

    @property
    def margin_pct(self) -> float:
        return float(self.store.get("mode_a_margin_pct", self.cfg.mode_a_margin_pct))

    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    def mode_label(self) -> str:
        if self.mode == "A":
            return f"A (protocol: {self.margin_pct:g}% margin, 15-25x)"
        return f"B (capped: {self.risk_pct:g}% of account at risk per trade)"

    # ----------------------------------------------------------- Module 10
    def start(self, balance_override: float | None, custom_margin: float | None) -> Proposal | str:
        now = self.now()
        wd = now.weekday()
        if wd >= 5:
            return "Weekend - Mon-Fri only. No new trades."
        friday = wd == 4
        if friday and now.hour >= self.cfg.friday_no_entry_after_hour:
            return f"Friday after {self.cfg.friday_no_entry_after_hour}:00 - no new entries (must close same day)."
        if self.store.trades("open") or self.store.trades("pending"):
            return "A trade is already open/pending. One at a time - check /status."

        acct = self.mkt.account(self.cfg.hl_account_address)
        api_bal = acct["account_value"]
        lines = []
        if api_bal <= 0 and acct["spot_usdc"] > 0:
            return (f"Your perps balance is $0 but you have ${acct['spot_usdc']:.2f} USDC in spot. "
                    "In the Hyperliquid app: Portfolio -> Transfer -> Spot to Perps, then try again.")
        balance = balance_override if balance_override else api_bal
        if balance_override and api_bal > 0 and abs(balance_override - api_bal) / api_bal > 0.05:
            lines.append(f"! You said ${balance_override:.2f}, API shows ${api_bal:.2f}. Sizing on ${balance:.2f}; "
                         f"margin capped at what's actually available (${acct['withdrawable']:.2f}).")
        if balance <= 0:
            return "Balance is $0 - deposit USDC to Hyperliquid first."

        # Module 1
        gate = st.btc_gate(self.cfg, self.mkt)
        lines.insert(0, f"BTC gate: {gate.state.upper()} - {gate.detail}")
        if gate.pump_check:
            pc = gate.pump_check
            lines.append("Post-pump check: " + ("REVERSAL BUILDING - " if pc["reversal_building"] else "")
                         + (", ".join(pc["flags"]) or "no reversal flags"))
        if gate.state == "skip":
            return "\n".join(lines + ["No trade today."])

        fng = self.mkt.fear_greed()
        # Module 2
        coins, notes, ratios = st.screener(self.cfg, self.mkt, self.store, friday, self.mode)
        if not coins:
            return "\n".join(lines + ["Screener found nothing that passes the filters."] + notes[:8])

        # Module 3/4
        cands = [st.deep_dive(self.cfg, self.mkt, c, gate, fng, ratios) for c in coins]
        rp = st.RunParams(balance, acct["withdrawable"], self.mode, self.margin_pct, self.risk_pct, custom_margin, friday)
        options: list[st.Option] = []
        rejects: list[str] = []
        for c in cands:
            o, r = st.build_options(self.cfg, c, gate, rp)
            options += o
            rejects += r
        if not options:
            return "\n".join(lines + ["No candidate produced a setup that passes the risk rules:"] + rejects[:10])

        context = {
            "time_local": now.isoformat(timespec="minutes"),
            "weekday": now.strftime("%A"),
            "friday_rules": friday,
            "btc_gate": {"state": gate.state, "btc_change_24h_pct": round(gate.btc_change, 2),
                         "btc_below_4h_support": gate.btc_break_support, "btc_iv": gate.btc_iv,
                         "post_pump_check": gate.pump_check},
            "fear_greed": fng,
            "sizing_mode": self.mode_label(),
            "candidates": [st.candidate_brief(c) for c in cands],
            "options": [st.option_brief(o) for o in options],
            "rejected_options": rejects,
            "lessons": self.store.active_lessons(),
        }
        try:
            verdict = self.analyst.pick(context)
        except Exception as e:  # noqa: BLE001
            log.exception("analyst failed")
            return "\n".join(lines + [f"Analyst (Claude) call failed: {e}. Not trading blind."])

        by_id = {o.id: o for o in options}
        picked = by_id.get(verdict.get("pick", "NONE"))
        if not picked:
            rank = "\n".join(f"  {r['coin']}: {r['score']:.1f} - {r['note']}" for r in verdict.get("ranking", [])[:5])
            return "\n".join(lines + ["Analyst passes - no trade.", verdict.get("rationale", ""), rank])
        chosen = [picked]
        alt = by_id.get(verdict.get("alternate", "NONE"))
        if alt and alt.setup.coin == picked.setup.coin and alt.setup.side != picked.setup.side:
            chosen.append(alt)

        cand = next(c for c in cands if c.coin == picked.setup.coin)
        text = self._card(lines, gate, fng, cand, chosen, verdict, friday)
        context["verdict"] = verdict
        self.pending = Proposal(uuid.uuid4().hex[:6], time.time(), chosen, text, context)
        return self.pending

    def _card(self, head, gate, fng, cand, chosen, verdict, friday) -> str:
        out = ["CONFIRMATION CARD", *head]
        if fng:
            out.append(f"Fear & Greed: {fng['value']} ({fng['label']})")
        d = cand.ctx
        out.append(f"\n{cand.coin} {d['mark']:.6g} ({d['change_pct']:+.2f}% 24h) | vol ${d['volume_usd'] / 1e6:.0f}M")
        if cand.flow:
            out.append(f"Buy/sell {cand.flow['ratio']:.2f}x ({cand.flow['source']}, {cand.flow['minutes']}m)")
        if cand.whale and cand.whale.get("ratio") is not None:
            w = cand.whale
            out.append(f"Whale L/S {w['ratio']:.2f} ({w['longs']}L/{w['shorts']}S of top {w['accounts_scanned']})")
        oi = cand.oi
        out.append("OI $%.1fM %s" % (oi["oi_usd"] / 1e6, " ".join(
            f"{k.replace('_pct', '')} {v:+.1f}%" for k, v in oi.items() if k.endswith("_pct")) or f"({oi.get('note', '')})"))
        out.append(f"Funding {d['funding_8h_pct']:+.4f}%/8h-equiv")
        if cand.iv:
            out.append(f"IV {cand.iv['label']} (rank {cand.iv['rank']:.0f}, {cand.iv['source']})")
        if cand.reversal_short:
            out.append("Reversal signals: " + "; ".join(cand.reversal_short))
        for i, o in enumerate(chosen, 1):
            s, z = o.setup, o.sizing
            tag = f"[{i}] " if len(chosen) > 1 else ""
            sgn = 1 if s.side == "long" else -1
            liq = max(z.liq_protocol, z.liq_exchange_est) if s.side == "long" else min(z.liq_protocol, z.liq_exchange_est)
            out += [
                f"\n{tag}{s.coin} {s.side.upper()}  - mode {self.mode_label()}",
                f"Entry (limit): {fmt(s.entry)}  <- 5m confluence {'+'.join(s.entry_methods)}",
                f"SL: {fmt(s.sl)} ({-sgn * s.sl_pct:+.2f}%)  <- {s.sl_anchor}",
                f"TP: {fmt(s.tp)} ({pct(s.tp, s.entry):+.2f}%)  <- {s.tp_anchor}   R/R 1:{s.rr:.2f}",
                f"Leverage {z.leverage}x isolated | margin ${z.margin:.2f} | size {z.qty:g} {s.coin} (${z.notional:.2f})",
                f"Liq ~{fmt(liq)} {'below' if s.side == 'long' else 'above'} SL - OK",
                f"At SL: -${z.loss_at_sl:.2f} ({z.loss_pct_of_balance:.1f}% of account, {z.loss_pct_of_margin:.0f}% of margin)",
                f"At TP: +${z.gain_at_tp:.2f} (+{z.gain_pct_of_balance:.1f}% of account)",
            ]
            if s.obstacles:
                out.append("Levels before TP: " + ", ".join(s.obstacles[:4]))
        out.append(f"\nAnalyst ({verdict.get('confidence', '?')}% conf): {verdict.get('rationale', '')}")
        for w in verdict.get("warnings", [])[:5]:
            out.append(f"! {w}")
        if friday:
            out.append("! FRIDAY: position will be force-closed at "
                       f"{self.cfg.friday_close_hour}:00 {self.cfg.timezone}")
        ttl = int(self.cfg.proposal_ttl_min)
        if len(chosen) > 1:
            out.append(f"\nReply  y 1 (long)  or  y 2 (short)  to place, n to cancel. Expires in {ttl} min.")
        else:
            out.append(f"\nReply  y  to place, n to cancel. Expires in {ttl} min.")
        return "\n".join(out)

    def confirm(self, choice: int | None) -> str:
        p = self.pending
        if not p:
            return "Nothing to confirm. Send: start ($balance)"
        if time.time() - p.created > self.cfg.proposal_ttl_min * 60:
            self.pending = None
            return "That card expired - prices move. Send start again."
        if len(p.options) > 1 and choice not in (1, 2):
            return "Two setups on this card - reply  y 1  (long) or  y 2  (short)."
        opt = p.options[(choice or 1) - 1]
        s, z = opt.setup, opt.sizing
        mark = self.mkt.ctxs()[s.coin]["mark"]
        if (s.side == "long" and mark <= s.sl) or (s.side == "short" and mark >= s.sl):
            self.pending = None
            return f"Price {mark:.6g} already through the SL - setup invalid. Send start again."
        try:
            res = self.trader.place_bracket(s.coin, s.side, z.qty, s.entry, s.sl, s.tp, z.leverage)
        except Exception as e:  # noqa: BLE001
            log.exception("place failed")
            return f"Order rejected: {e}"
        self.pending = None
        tid = self.store.add_trade(
            coin=s.coin, side=s.side, status="open" if res["filled_now"] else "pending",
            entry=s.entry, sl=s.sl, tp=s.tp, qty=z.qty, leverage=z.leverage, margin=z.margin,
            entry_oid=res["entry_oid"], filled_at=time.time() if res["filled_now"] else None,
            plan={"card": p.text, "option": st.option_brief(opt), "context": p.context},
        )
        state = "FILLED" if res["filled_now"] else "resting"
        return (f"Placed #{tid}: {s.coin} {s.side.upper()} limit {fmt(s.entry)} x {z.qty:g} ({state}), "
                f"SL {fmt(s.sl)} / TP {fmt(s.tp)} attached, {z.leverage}x isolated.\n"
                f"Unfilled entries auto-cancel after {self.cfg.entry_ttl_hours:g}h.")

    # ----------------------------------------------------------- Module 8
    def sync(self) -> list[str]:
        """Detect fills/closes, guarantee SL/TP on open positions, run studies."""
        msgs: list[str] = []
        acct = self.mkt.account(self.cfg.hl_account_address)
        positions = {p["coin"]: p for p in acct["positions"]}
        orders = self.trader.open_orders()

        for t in self.store.trades("pending"):
            pos = positions.get(t["coin"])
            try:
                stt = self.trader.order_status(int(t["entry_oid"])) if t["entry_oid"] else {}
            except Exception:  # noqa: BLE001
                stt = {}
            ostatus = (stt.get("order") or {}).get("status")
            if pos or ostatus == "filled":
                self.store.update_trade(t["id"], status="open", filled_at=time.time())
                msgs.append(f"#{t['id']} {t['coin']} entry filled at ~{fmt(pos['entry']) if pos else fmt(t['entry'])}. SL/TP live.")
            elif ostatus in ("canceled", "rejected", "marginCanceled", "reduceOnlyCanceled") or (stt.get("status") == "unknownOid"):
                self.store.update_trade(t["id"], status="cancelled", closed_at=time.time(), close_reason=ostatus or "unknown")
                msgs.append(f"#{t['id']} {t['coin']} entry order {ostatus or 'gone'} - no position.")

        for t in self.store.trades("open"):
            pos = positions.get(t["coin"])
            if pos:
                has_sl = any(o["coin"] == t["coin"] and o.get("isTrigger") and "Stop" in o.get("orderType", "") for o in orders)
                if not has_sl and t["filled_at"] and time.time() - t["filled_at"] > 60:
                    try:
                        self.trader.protect_position(t["coin"], t["side"], pos["size"], t["sl"], t["tp"])
                        msgs.append(f"#{t['id']} {t['coin']}: SL/TP were missing - re-attached for {pos['size']:g}.")
                    except Exception as e:  # noqa: BLE001
                        msgs.append(f"!! #{t['id']} {t['coin']}: NO STOP LOSS and re-attach failed: {e}. Check the app NOW.")
                continue
            msgs += self._on_closed(t)
        return msgs

    def _on_closed(self, t: dict) -> list[str]:
        since = t["filled_at"] or t["created_at"]
        fills = [f for f in self.trader.fills_since(since - 5) if f["coin"] == t["coin"]]
        closing = [f for f in fills if "Close" in f.get("dir", "")]
        pnl = sum(float(f.get("closedPnl", 0)) for f in closing) - sum(float(f.get("fee", 0)) for f in fills)
        qty = sum(float(f["sz"]) for f in closing)
        exit_px = sum(float(f["px"]) * float(f["sz"]) for f in closing) / qty if qty else None
        self.trader.cancel_all(t["coin"])  # leftover TP or SL
        reason = t.get("close_reason")
        if exit_px and not reason:
            if abs(exit_px - t["tp"]) / t["tp"] < 0.004:
                reason = "tp"
            elif abs(exit_px - t["sl"]) / t["sl"] < 0.004:
                reason = "sl"
            else:
                reason = "manual/other"
        self.store.update_trade(t["id"], status="closed", closed_at=time.time(), exit_px=exit_px, pnl=pnl,
                                close_reason=reason or "unknown")
        msg = [f"#{t['id']} {t['coin']} {t['side'].upper()} CLOSED ({reason}) at {fmt(exit_px) if exit_px else '?'} "
               f"-> PnL ${pnl:+.2f}"]
        try:
            msg.append(self.study(t["id"]))
        except Exception as e:  # noqa: BLE001
            log.exception("study failed")
            msg.append(f"(post-trade study failed: {e})")
        return ["\n".join(msg)]

    def manage(self) -> list[str]:
        """Time rules: entry TTL, 2-day max hold, Friday alarm + forced close, no weekend holds."""
        msgs: list[str] = []
        now = self.now()
        wd = now.weekday()
        for t in self.store.trades("pending"):
            if time.time() - t["created_at"] > self.cfg.entry_ttl_hours * 3600 or wd >= 5 or (
                    wd == 4 and now.hour >= self.cfg.friday_close_hour):
                try:
                    self.trader.cancel(t["coin"], int(t["entry_oid"]))
                except Exception as e:  # noqa: BLE001
                    log.warning("cancel: %s", e)
                self.store.update_trade(t["id"], status="cancelled", closed_at=time.time(), close_reason="entry expired")
                msgs.append(f"#{t['id']} {t['coin']} entry not filled in time - cancelled.")

        opens = self.store.trades("open")
        for t in opens:  # partially-filled entries: drop the unfilled remainder after the TTL
            if t["entry_oid"] and time.time() - t["created_at"] > self.cfg.entry_ttl_hours * 3600 and not self.store.get(
                    f"rem_cancelled:{t['id']}"):
                try:
                    self.trader.cancel(t["coin"], int(t["entry_oid"]))
                except Exception:  # noqa: BLE001
                    pass  # already filled
                self.store.set(f"rem_cancelled:{t['id']}", True)
        if wd == 4 and now.hour >= self.cfg.friday_alarm_hour and opens and self._friday_alarm_day != now.date().isoformat():
            self._friday_alarm_day = now.date().isoformat()
            msgs.append(f"ALARM: holding {', '.join(t['coin'] for t in opens)} into Friday afternoon. "
                        f"Auto-close at {self.cfg.friday_close_hour}:00. Trail or close now if you prefer (/close).")
        for t in opens:
            why = None
            if wd >= 5 or (wd == 4 and now.hour >= self.cfg.friday_close_hour):
                why = "friday close"
            elif t["filled_at"] and time.time() - t["filled_at"] > self.cfg.max_hold_hours * 3600:
                why = "max hold"
            if why:
                try:
                    self.store.update_trade(t["id"], close_reason=why)
                    self.trader.close(t["coin"])
                    msgs.append(f"#{t['id']} {t['coin']}: closing at market ({why}).")
                except Exception as e:  # noqa: BLE001
                    msgs.append(f"!! #{t['id']} {t['coin']}: {why} close FAILED: {e}")
        return msgs

    def pump_watch(self) -> str | None:
        btc = self.mkt.ctxs()["BTC"]
        if btc["change_pct"] <= self.cfg.btc_pump_check_pct or time.time() - self._last_pump_alert < 4 * 3600:
            return None
        pc = st.post_pump_check(self.cfg, self.mkt, "BTC")
        self._last_pump_alert = time.time()
        head = "REVERSAL CONDITIONS BUILDING" if pc["reversal_building"] else "no reversal yet"
        return (f"BTC pump {pc['change_pct']:+.2f}% - post-pump check: {head}\n"
                f"Funding {pc['funding_8h_pct']:+.4f}%/8h | whale L/S {pc['whale_ls'] if pc['whale_ls'] is None else round(pc['whale_ls'], 2)}"
                f" | nearest resistance {pc['nearest_resistance']}\n" + ("; ".join(pc["flags"]) or ""))

    # ----------------------------------------------------------- Module 9
    def study(self, tid: int) -> str:
        t = self.store.trade(tid)
        coin = t["coin"]
        since = t["filled_at"] or t["created_at"]
        c5 = [c for c in self.mkt.candles(coin, "5m", 600) if c["t"] >= since * 1000]
        mark = self.mkt.ctxs()[coin]["mark"]
        lv = build_levels(self.mkt.candles(coin, "5m", 300), self.mkt.candles(coin, "4h", 180), self.mkt.candles(coin, "1d", 90))
        sup, res = nearest(lv["4h"], mark, above=False), nearest(lv["4h"], mark, above=True)
        ctx = {
            "trade": {k: t[k] for k in ("coin", "side", "entry", "sl", "tp", "qty", "leverage", "margin",
                                        "exit_px", "pnl", "close_reason")},
            "held_hours": round(((t["closed_at"] or time.time()) - since) / 3600, 1),
            "after_entry": {"max_high": max((c["h"] for c in c5), default=None),
                            "min_low": min((c["l"] for c in c5), default=None)},
            "current_price": mark,
            "current_4h_support": sup.label() if sup else None,
            "current_4h_resistance": res.label() if res else None,
            "plan": t["plan"],
            "existing_rules": self.store.active_lessons(),
        }
        r = self.analyst.study(ctx)
        self.store.update_trade(tid, study=r)
        out = [f"Study: {r['what_happened']}"]
        if r["went_right"]:
            out.append("Right: " + "; ".join(r["went_right"]))
        if r["went_wrong"]:
            out.append("Wrong: " + "; ".join(r["went_wrong"]))
        out.append(f"Lesson: {r['lesson']}")
        if r["propose_rule"] and r["rule_text"].strip():
            lid = self.store.add_lesson(r["rule_text"].strip(), tid)
            out.append(f"Proposed new rule #{lid}: {r['rule_text']}\n/approve {lid} to adopt, /reject {lid} to drop.")
        return "\n".join(out)

    # ----------------------------------------------------------- status
    def status(self) -> str:
        acct = self.mkt.account(self.cfg.hl_account_address)
        out = [f"Network: {'MAINNET' if self.cfg.is_mainnet else 'testnet'} | sizing mode {self.mode_label()}",
               f"Account value ${acct['account_value']:.2f} | available ${acct['withdrawable']:.2f}"
               + (f" | spot USDC ${acct['spot_usdc']:.2f}" if acct["spot_usdc"] else "")]
        for p in acct["positions"]:
            out.append(f"{p['coin']} {p['side']} {p['size']:g} @ {fmt(p['entry'])} | uPnL ${p['upnl']:+.2f} | liq {p['liq']}")
        for t in self.store.trades("pending"):
            out.append(f"#{t['id']} pending {t['coin']} {t['side']} limit {fmt(t['entry'])}")
        if len(out) == 2:
            out.append("No positions or pending entries.")
        return "\n".join(out)

    def journal(self, n: int = 10) -> str:
        rows = [t for t in self.store.trades(limit=50) if t["status"] in ("closed", "open", "pending")][:n]
        if not rows:
            return "Journal is empty."
        total = sum(t["pnl"] or 0 for t in self.store.trades("closed", limit=1000))
        lines = [f"Realized PnL (all closed): ${total:+.2f}"]
        for t in rows:
            pnl = f"${t['pnl']:+.2f}" if t["pnl"] is not None else ""
            lines.append(f"#{t['id']} {t['coin']} {t['side']} {t['status']} {t['close_reason'] or ''} {pnl}")
        return "\n".join(lines)
