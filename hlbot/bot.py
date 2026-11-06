"""HEVI's Telegram interface - Module 12 command set. Only the chat in TELEGRAM_CHAT_ID can control the bot.

With autopilot on (protocol.toml [autopilot], the v3.1 default) HEVI scans, places, manages and reports on its
own; the commands below are for watching it, pausing it, or trading by hand.

  start ($X)                  pre-flight -> BTC gate -> sweeps -> deep dive -> filters -> rank -> confirmation card
  y | y 1 | y 2               execute (Module 10 checklist). 1 = long, 2 = short when both are shown
  nah | skip | try another    re-sweep for fresh candidates (max 2 per session)
  n | no                      discard the card
  how is it going             live price, uPnL in $ and R, distance to TP/SL, funding, time left
  review                      Module 11 stats from the log
  risk X%                     set risk within the 15% ceiling
  override: [reason]          take the closest candidate blocked only by soft rules (never hard rules)
  /status /journal /close COIN [reason] /cancel /lessons /approve N /reject N /pump /paperreset N /help
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from telegram import BotCommand, Update
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import CFG
from .engine import Engine, Proposal

log = logging.getLogger(__name__)

HELP = """HEVI - Hyperliquid trading bot, protocol v{v}
{mode}

Autopilot scans every {scan:g} min (Mon-Thu UTC) and places any setup that passes every rule. You don't need to
do anything; you'll get a message for every trade and a daily report at {report}:00 UTC.

/status - mode, equity, peak, drawdown, breakers, positions
/pause - stop opening new trades (open positions stay protected) | /resume
how is it going - live price, uPnL in $ and R, distance to TP/SL, funding, time left
review - stats in R (also /review)

Manual trading (works with autopilot paused):
start ($14) - full run -> confirmation card ($14 = your equity; the API figure is checked)
y / y 1 / y 2 - place the trade (1 = long, 2 = short when both are shown)
nah / skip / try another - re-sweep fresh candidates (max 2 per session)
n - discard the card
how is it going - live price, uPnL in $ and R, distance to TP/SL, funding, time left
review - stats in R (also /review)
risk 7% - set risk per trade (max 15%)
override: [reason] - take the closest setup blocked only by soft rules. Hard rules never move.

/status - equity, peak, drawdown, breakers, positions
/journal - recent trades in R
/close SOL [reason] - market-close a position (logged as discretionary)
/cancel - cancel resting entry orders (TP/SL stay on)
/lessons - rules and hypotheses; /approve N promotes one (needs 5+ supporting trades and 20 closed)
/pump - BTC post-pump check now
/paperreset 100 - paper mode: restart the simulated account"""

COMMANDS = [("status", "Mode, equity, drawdown, breakers, positions"), ("review", "Track record in R"),
            ("journal", "Recent trades"), ("pause", "Stop opening new trades"), ("resume", "Resume autopilot"),
            ("close", "Close a position: /close SOL"), ("cancel", "Cancel resting entries"),
            ("lessons", "Rules and hypotheses"), ("pump", "BTC post-pump check"), ("help", "How HEVI works")]

START_RE = re.compile(r"^\s*start\b(?:\s*\(?\s*\$?\s*([\d.]+)\s*\)?)?(.*)$", re.I)
YES_RE = re.compile(r"^\s*y(?:es)?\s*([12])?\s*$", re.I)
RISK_RE = re.compile(r"^\s*risk\s+([\d.]+)\s*%?\s*$", re.I)
OVERRIDE_RE = re.compile(r"^\s*override\s*:\s*(.*)$", re.I | re.S)
RESWEEP = {"nah", "skip", "try another", "another"}
HOW = re.compile(r"^\s*how('?s| is) it going\??\s*$", re.I)


def parse(text: str) -> tuple[str, object]:
    """Text command -> (name, argument). Pure, so it is unit-tested."""
    t = (text or "").strip()
    low = t.lower().rstrip("!.")
    if m := START_RE.match(t):
        if "margin" in (m.group(2) or "").lower():
            return "start_margin", None
        return "start", float(m.group(1)) if m.group(1) else None
    if m := YES_RE.match(t):
        return "yes", int(m.group(1)) if m.group(1) else None
    if low in ("n", "no"):
        return "discard", None
    if low in RESWEEP:
        return "resweep", None
    if HOW.match(t):
        return "how", None
    if low == "review":
        return "review", None
    if m := RISK_RE.match(t):
        return "risk", float(m.group(1))
    if m := OVERRIDE_RE.match(t):
        return "override", m.group(1).strip()
    return "unknown", None


class Bot:
    def __init__(self):
        self.engine = Engine(CFG)
        self.chat_id = CFG.telegram_chat_id
        self._busy = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app: Application | None = None
        self._fills_on = False

    # ------------------------------------------------------------ plumbing
    def _authorized(self, update: Update) -> bool:
        return bool(self.chat_id) and str(update.effective_chat.id) == str(self.chat_id)

    async def _reply(self, update: Update, text: str) -> None:
        for i in range(0, len(text), 4000):
            await update.effective_chat.send_message(text[i: i + 4000], disable_web_page_preview=True)

    async def _push(self, app: Application, text: str, delays: tuple = (3, 10, 30)) -> bool:
        """Send to the owner's chat. Phone networks drop out: retry with backoff, and if Telegram stays unreachable,
        log one line and carry on - a lost message must never stop trading or management."""
        if not (self.chat_id and text):
            return False
        for i in range(0, len(text), 4000):
            chunk = text[i: i + 4000]
            for attempt in range(len(delays) + 1):
                try:
                    await app.bot.send_message(self.chat_id, chunk, disable_web_page_preview=True)
                    break
                except RetryAfter as e:  # Telegram flood control: wait as told
                    ra = e.retry_after
                    await asyncio.sleep(ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra))
                except (BadRequest, Forbidden) as e:  # wrong chat id, bot blocked: retrying won't help
                    log.warning("Telegram refused the message: %s", e)
                    return False
                except NetworkError as e:  # timeouts, no connection
                    if attempt == len(delays):
                        log.warning("Telegram unreachable after %d tries, message not delivered: %s", attempt + 1, e)
                        return False
                    await asyncio.sleep(delays[attempt])
                except TelegramError as e:  # e.g. wrong chat id: retrying won't help
                    log.warning("Telegram refused the message: %s", e)
                    return False
        return True

    async def on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Network blips get one log line; anything else gets the full traceback."""
        if isinstance(ctx.error, NetworkError) and not isinstance(ctx.error, (BadRequest, Forbidden)):
            log.warning("Telegram network error (will retry automatically): %s", ctx.error)
        else:
            log.error("Unhandled error", exc_info=ctx.error)

    async def _guard(self, update: Update) -> bool:
        if self._authorized(update):
            return True
        await update.effective_chat.send_message(
            f"Not authorized. Your chat id is {update.effective_chat.id} - set TELEGRAM_CHAT_ID to it and redeploy.")
        return False

    async def _run(self, update: Update, fn, *args, busy_msg: str | None = None) -> None:
        if self._busy.locked():
            await self._reply(update, "Already running - hang on.")
            return
        async with self._busy:
            if busy_msg:
                await self._reply(update, busy_msg)
            try:
                res = await asyncio.to_thread(fn, *args)
            except Exception as e:  # noqa: BLE001
                log.exception("command failed")
                res = f"Failed: {e}"
        await self._reply(update, res.text if isinstance(res, Proposal) else str(res))

    # ------------------------------------------------------------ text commands
    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        cmd, arg = parse(update.message.text or "")
        e = self.engine
        if cmd == "start":
            await self._run(update, e.start, arg,
                            busy_msg="Running: pre-flight -> BTC gate -> sweeps -> deep dive -> filters... (~1-2 min)")
        elif cmd == "start_margin":
            await self._reply(update, "Custom margin was retired in v3: every trade is sized by Module 7 only "
                                      "(risk % of equity at the SL). Send  start ($equity)  or set  risk X%.")
        elif cmd == "yes":
            await self._run(update, e.confirm, arg)
        elif cmd == "discard":
            e.pending = None
            await self._reply(update, "Card discarded.")
        elif cmd == "resweep":
            await self._run(update, e.resweep, busy_msg="Re-sweeping for fresh candidates...")
        elif cmd == "how":
            await self._run(update, e.how_is_it_going)
        elif cmd == "review":
            await self._run(update, e.review)
        elif cmd == "risk":
            await self._reply(update, e.set_risk(arg))
        elif cmd == "override":
            await self._run(update, e.override, arg)
        else:
            await self._reply(update, "Send  start ($equity)  or /help")

    # ------------------------------------------------------------ slash commands
    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            e, A = self.engine, self.engine.P.autopilot
            await self._reply(update, HELP.format(v=e.P.version, mode=f"Mode: {e.label()}", scan=A.scan_every_min,
                                                  report=A.report_hour_utc))

    async def cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._reply(update, self.engine.set_paused(True))

    async def cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._reply(update, self.engine.set_paused(False))

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._run(update, self.engine.status)

    async def cmd_journal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._run(update, self.engine.journal)

    async def cmd_review(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._run(update, self.engine.review)

    async def cmd_risk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        try:
            await self._reply(update, self.engine.set_risk(float(ctx.args[0].rstrip("%"))))
        except Exception:  # noqa: BLE001
            await self._reply(update, "Usage: /risk 10   (percent of equity lost at the SL, max 15)")

    async def cmd_close(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if not ctx.args:
            await self._reply(update, "Usage: /close SOL [reason]")
            return
        coin = ctx.args[0] if ctx.args[0].startswith("k") else ctx.args[0].upper()
        await self._run(update, self.engine.close_manual, coin, " ".join(ctx.args[1:]) or None)

    async def cmd_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Cancel resting ENTRY orders only. TP/SL are never removed (Module 9)."""
        if not await self._guard(update):
            return

        def go() -> str:
            n = 0
            for o in self.engine.trader.open_orders():
                if not o.get("isTrigger"):
                    self.engine.trader.cancel(o["coin"], int(o["oid"]))
                    n += 1
            return f"Cancelled {n} resting entry order(s). TP/SL on open positions are untouched."

        await self._run(update, go)

    async def cmd_lessons(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        st = self.engine.store
        lines = ["Rules (carried from v1/v2):"] + [f"- {x}" for x in st.active_lessons()]
        hyps = st.lessons()
        if hyps:
            lines.append("\nHypotheses (need 5+ supporting trades, promoted only at a 20-trade review):")
            lines += [f"#{h['id']} [{h['status']}] ({len(h['support'])} trades) {h['text']}" for h in hyps]
        await self._reply(update, "\n".join(lines))

    async def cmd_approve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        P, st = self.engine.P, self.engine.store
        try:
            h = st.lesson(int(ctx.args[0]))
        except Exception:  # noqa: BLE001
            h = None
        if not h:
            await self._reply(update, "Usage: /approve N")
            return
        closed = len(st.closed(self.engine._since()))
        if len(h["support"]) < P.review.hypothesis_min_trades:
            await self._reply(update, f"Hypothesis #{h['id']} has {len(h['support'])} supporting trades; "
                                      f"{P.review.hypothesis_min_trades}+ needed before it can become a rule.")
            return
        if closed < P.review.every_n_trades:
            await self._reply(update, f"Rules change only at {P.review.every_n_trades}-trade reviews ({closed} closed so far).")
            return
        st.set_lesson(h["id"], "rule")
        st.add_event("rule_change", f"hypothesis #{h['id']} promoted: {h['text']}")
        await self._reply(update, f"Hypothesis #{h['id']} promoted to a rule. Bump `version` in protocol.toml "
                                  f"(v{P.version} -> next) when you change thresholds to match it.")

    async def cmd_reject(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        try:
            ok = self.engine.store.set_lesson(int(ctx.args[0]), "rejected")
        except Exception:  # noqa: BLE001
            ok = False
        await self._reply(update, "Hypothesis rejected." if ok else "Usage: /reject N")

    async def cmd_pump(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return

        def go() -> str:
            self.engine._last_pump_alert = 0
            return self.engine.pump_watch() or "BTC is not in the +3% band - no post-pump check needed."

        await self._run(update, go)

    async def cmd_paperreset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        e = self.engine
        if not e.paper:
            await self._reply(update, "Only available in paper mode (PAPER_TRADING=true).")
            return
        if e.store.trades("open") or e.store.trades("pending"):
            await self._reply(update, "Close or cancel the open/pending paper trade first.")
            return
        try:
            v = float(ctx.args[0]) if ctx.args else CFG.paper_balance
            assert 1 <= v <= 1_000_000
        except Exception:  # noqa: BLE001
            await self._reply(update, "Usage: /paperreset 100   ($1 - $1,000,000)")
            return
        e.trader.reset(v)
        await self._reply(update, f"Paper account reset to ${v:,.2f}. Reviews, breakers and the go-live gate now "
                                  "count from here.")

    # ------------------------------------------------------------ jobs
    async def _job(self, ctx: ContextTypes.DEFAULT_TYPE, fns) -> None:
        msgs: list[str] = []
        for fn in fns:
            try:
                out = await asyncio.to_thread(fn)
                if isinstance(out, str):
                    out = [out]
                msgs += out or []
            except Exception as e:  # noqa: BLE001
                log.exception("%s failed", getattr(fn, "__name__", fn))
                if int(time.time()) % 900 < 20:  # at most ~every 15 min
                    msgs.append(f"({getattr(fn, '__name__', 'job')} error: {e})")
        for m in msgs:
            await self._push(ctx.application, m)

    async def job_sync(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if self._busy.locked():
            return
        await self._job(ctx, [self.engine.manage, self.engine.sync])

    async def job_pump(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._job(ctx, [self.engine.pump_watch])

    async def job_hourly(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._job(ctx, [self.engine.hourly])

    async def job_autopilot(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._ensure_fill_stream()
        if self._busy.locked():
            return
        async with self._busy:  # a scan and a manual command never interleave
            await self._job(ctx, [self.engine.autopilot_tick])

    # ------------------------------------------------------------ instant protection on fills
    def _ensure_fill_stream(self) -> None:
        """Live mode: subscribe to the account's fills so a (partial) fill gets TP/SL within a second, instead of
        waiting for the 20-second sync."""
        if self._fills_on or not self.engine.live or not CFG.hl_account_address:
            return
        try:
            self.engine.mkt.flow.subscribe_extra({"type": "userFills", "user": CFG.hl_account_address}, self._on_fill)
            self._fills_on = True
        except Exception:  # noqa: BLE001
            log.exception("fill stream subscribe failed (20s sync still runs)")

    def _on_fill(self, msg: dict) -> None:
        if self._loop and self._app and not (msg.get("data") or {}).get("isSnapshot"):
            asyncio.run_coroutine_threadsafe(self._instant_sync(), self._loop)

    async def _instant_sync(self) -> None:
        if self._app is None or self._busy.locked():
            return

        class _Ctx:
            application = self._app

        await self._job(_Ctx(), [self.engine.sync])

    async def on_startup(self, app: Application) -> None:
        e = self.engine
        self._loop, self._app = asyncio.get_running_loop(), app
        try:  # the name, description and command menu people see in Telegram
            if (await app.bot.get_my_name()).name != "HEVI":
                await app.bot.set_my_name("HEVI")
            await app.bot.set_my_short_description("HEVI - autonomous Hyperliquid trading bot")
            await app.bot.set_my_description(
                "HEVI trades Hyperliquid perpetuals on its own under a strict risk protocol: every trade has its "
                "stop-loss and take-profit on the exchange, risk is capped per trade, and circuit breakers pause it "
                "after losses. Only its owner can control it.")
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
        except Exception:  # noqa: BLE001
            log.warning("could not set the bot's Telegram profile (rate limit?) - continuing", exc_info=True)
        auto = ("autopilot ON: scanning every %g min, Mon-Thu UTC" % e.P.autopilot.scan_every_min
                if e.autopilot_on else "autopilot PAUSED" if e.paused else "autopilot off")
        await self._push(app, f"HEVI online - {e.label()}.\nProtocol v{e.P.version}, risk {e.user_risk_pct:g}% per "
                              f"trade, {auto}. First scan after a {e.P.autopilot.warmup_min:g}-min warm-up. /help")

    def run(self) -> None:
        app = (Application.builder().token(CFG.telegram_token).post_init(self.on_startup)
               # mobile networks are slow to connect: PTB's 5-second default times out too easily
               .connect_timeout(20).read_timeout(30).write_timeout(30).pool_timeout(30)
               .get_updates_connect_timeout(20).get_updates_read_timeout(30)
               .build())
        app.add_error_handler(self.on_error)
        for name, fn in {
            "help": self.cmd_help, "start": self.cmd_help, "status": self.cmd_status, "journal": self.cmd_journal,
            "review": self.cmd_review, "stats": self.cmd_review, "risk": self.cmd_risk, "close": self.cmd_close,
            "cancel": self.cmd_cancel, "lessons": self.cmd_lessons, "approve": self.cmd_approve,
            "reject": self.cmd_reject, "pump": self.cmd_pump, "paperreset": self.cmd_paperreset,
            "pause": self.cmd_pause, "resume": self.cmd_resume,
        }.items():
            app.add_handler(CommandHandler(name, fn))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        jq = app.job_queue
        jq.run_repeating(self.job_sync, interval=20, first=20)   # partial fills get TP/SL within ~20s
        jq.run_repeating(self.job_pump, interval=900, first=120)
        jq.run_repeating(self.job_hourly, interval=3600, first=30)
        jq.run_repeating(self.job_autopilot, interval=60, first=45)
        app.run_polling(allowed_updates=Update.ALL_TYPES)
