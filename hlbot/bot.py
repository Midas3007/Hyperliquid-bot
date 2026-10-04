"""Telegram interface - run the whole challenge from your phone.

  start ($14)            run the autopilot (Modules 1-7) and get a confirmation card
  start ($14) margin $5  same, with a custom margin amount
  y | y 1 | y 2 | n      confirm / pick long or short / cancel the card
  /status /journal /close COIN /cancel /mode A|B /risk 15 /size 80 /lessons /approve N /reject N /pump /help
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import CFG
from .engine import Engine, Proposal

log = logging.getLogger(__name__)

HELP = """Hyperliquid compounding bot

start ($14) - run BTC gate -> screener -> deep dive -> pick -> confirmation card
start ($14) margin $5 - custom margin
y / y 1 / y 2 - place the trade (1=long, 2=short when both are shown)
n - discard the card

/status - balance, positions, pending orders
/journal - recent trades + realized PnL
/close COIN - market-close a position (cancels its TP/SL)
/cancel - cancel all resting orders
/mode A or /mode B - protocol sizing vs capped-risk sizing
/risk 15 - mode B: % of account lost if SL hits
/size 80 - mode A: % of balance used as margin
/lessons - rules learned from post-trade studies
/approve N, /reject N - accept or drop a proposed rule
/pump - run the BTC post-pump reversal check now"""

START_RE = re.compile(r"^\s*start\b(?:\s*\(\s*\$?\s*([\d.]+)\s*\))?(?:.*?margin\s*\$?\s*([\d.]+))?", re.I)
YES_RE = re.compile(r"^\s*y(?:es)?\s*([12])?\s*$", re.I)


class Bot:
    def __init__(self):
        self.engine = Engine(CFG)
        self.chat_id = CFG.telegram_chat_id
        self._busy = asyncio.Lock()

    # ------------------------------------------------------------ plumbing
    def _authorized(self, update: Update) -> bool:
        return bool(self.chat_id) and str(update.effective_chat.id) == str(self.chat_id)

    async def _reply(self, update: Update, text: str) -> None:
        for i in range(0, len(text), 4000):
            await update.effective_chat.send_message(text[i : i + 4000], disable_web_page_preview=True)

    async def _push(self, app: Application, text: str) -> None:
        if self.chat_id and text:
            for i in range(0, len(text), 4000):
                await app.bot.send_message(self.chat_id, text[i : i + 4000], disable_web_page_preview=True)

    async def _guard(self, update: Update) -> bool:
        if self._authorized(update):
            return True
        await update.effective_chat.send_message(
            f"Not authorized. Your chat id is {update.effective_chat.id} - set TELEGRAM_CHAT_ID to it and redeploy."
        )
        return False

    # ------------------------------------------------------------ text
    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        text = update.message.text or ""
        if m := START_RE.match(text):
            bal = float(m.group(1)) if m.group(1) else None
            margin = float(m.group(2)) if m.group(2) else None
            if self._busy.locked():
                await self._reply(update, "Already running - hang on.")
                return
            async with self._busy:
                await self._reply(update, "Running: BTC gate -> screener -> deep dive -> analyst... (~1-2 min)")
                try:
                    res = await asyncio.to_thread(self.engine.start, bal, margin)
                except Exception as e:  # noqa: BLE001
                    log.exception("start failed")
                    res = f"Run failed: {e}"
            await self._reply(update, res.text if isinstance(res, Proposal) else res)
            return
        if m := YES_RE.match(text):
            choice = int(m.group(1)) if m.group(1) else None
            async with self._busy:
                res = await asyncio.to_thread(self.engine.confirm, choice)
            await self._reply(update, res)
            return
        if text.strip().lower() in ("n", "no"):
            self.engine.pending = None
            await self._reply(update, "Card discarded.")
            return
        await self._reply(update, "Send  start ($balance)  or /help")

    # ------------------------------------------------------------ commands
    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._reply(update, HELP)

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._reply(update, await asyncio.to_thread(self.engine.status))

    async def cmd_journal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            await self._reply(update, await asyncio.to_thread(self.engine.journal))

    async def cmd_close(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if not ctx.args:
            await self._reply(update, "Usage: /close BTC")
            return
        coin = ctx.args[0]
        coin = coin if coin.startswith("k") else coin.upper()
        for t in self.engine.store.trades("open"):
            if t["coin"] == coin:
                self.engine.store.update_trade(t["id"], close_reason="manual")
        try:
            await asyncio.to_thread(self.engine.trader.close, coin)
            await self._reply(update, f"{coin} market close sent. Result + study will follow.")
        except Exception as e:  # noqa: BLE001
            await self._reply(update, f"Close failed: {e}")

    async def cmd_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._guard(update):
            n = await asyncio.to_thread(self.engine.trader.cancel_all)
            await self._reply(update, f"Cancelled {n} order(s). Note: this also removes TP/SL on open positions - "
                                      "the bot re-attaches SL/TP within a minute.")

    async def cmd_mode(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if ctx.args and ctx.args[0].upper() in ("A", "B"):
            self.engine.store.set("mode", ctx.args[0].upper())
            warn = ("\nMode A risks ~30-75% of the account per stop-out. You chose it - it's on."
                    if ctx.args[0].upper() == "A" else "")
            await self._reply(update, f"Sizing mode: {self.engine.mode_label()}{warn}")
        else:
            await self._reply(update, f"Current: {self.engine.mode_label()}\nUse /mode A or /mode B")

    async def cmd_risk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        try:
            v = float(ctx.args[0])
            assert 1 <= v <= 50
        except Exception:  # noqa: BLE001
            await self._reply(update, "Usage: /risk 15   (1-50, % of account lost at SL in mode B)")
            return
        self.engine.store.set("mode_b_risk_pct", v)
        await self._reply(update, f"Mode B risk set to {v:g}% per trade.")

    async def cmd_size(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        try:
            v = float(ctx.args[0])
            assert 1 <= v <= 85
        except Exception:  # noqa: BLE001
            await self._reply(update, "Usage: /size 80   (1-85, % of balance used as margin in mode A)")
            return
        self.engine.store.set("mode_a_margin_pct", v)
        await self._reply(update, f"Mode A margin set to {v:g}% of balance.")

    async def cmd_lessons(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        rows = self.engine.store.lessons()
        lines = ["Hardcoded:"] + [f"- {x}" for x in self.engine.store.active_lessons()[:6]]
        if rows:
            lines.append("\nLearned:")
            lines += [f"#{r['id']} [{r['status']}] {r['text']}" for r in rows]
        await self._reply(update, "\n".join(lines))

    async def _set_lesson(self, update: Update, ctx, status: str) -> None:
        if not await self._guard(update):
            return
        try:
            ok = self.engine.store.set_lesson(int(ctx.args[0]), status)
        except Exception:  # noqa: BLE001
            ok = False
        await self._reply(update, f"Rule {status}." if ok else "Usage: /approve N  or  /reject N")

    async def cmd_approve(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_lesson(update, ctx, "active")

    async def cmd_reject(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_lesson(update, ctx, "rejected")

    async def cmd_pump(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        from . import strategy as st

        pc = await asyncio.to_thread(st.post_pump_check, CFG, self.engine.mkt, "BTC")
        await self._reply(update, f"BTC {pc['change_pct']:+.2f}% | reversal building: {pc['reversal_building']}\n"
                                  f"Flags: {', '.join(pc['flags']) or 'none'}\nNearest resistance: {pc['nearest_resistance']}")

    # ------------------------------------------------------------ jobs
    async def job_minute(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            msgs = await asyncio.to_thread(self.engine.manage)
            msgs += await asyncio.to_thread(self.engine.sync)
        except Exception as e:  # noqa: BLE001
            log.exception("sync failed")
            msgs = []
            if int(time.time()) % 900 < 60:  # don't spam: at most ~every 15 min
                msgs = [f"(sync error: {e})"]
        for m in msgs:
            await self._push(ctx.application, m)

    async def job_pump(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            m = await asyncio.to_thread(self.engine.pump_watch)
            if m:
                await self._push(ctx.application, m)
        except Exception:  # noqa: BLE001
            log.exception("pump watch failed")

    async def job_hourly(self, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await asyncio.to_thread(self.engine.mkt.record_oi)
        except Exception:  # noqa: BLE001
            log.exception("oi snapshot failed")

    async def on_startup(self, app: Application) -> None:
        net = "MAINNET (real money)" if CFG.is_mainnet else "testnet"
        await self._push(app, f"Bot online - {net}. Sizing mode {self.engine.mode_label()}. Send /help.")

    def run(self) -> None:
        app = Application.builder().token(CFG.telegram_token).post_init(self.on_startup).build()
        for name, fn in {
            "help": self.cmd_help, "start": self.cmd_help, "status": self.cmd_status, "journal": self.cmd_journal,
            "close": self.cmd_close, "cancel": self.cmd_cancel, "mode": self.cmd_mode, "risk": self.cmd_risk,
            "size": self.cmd_size, "lessons": self.cmd_lessons, "approve": self.cmd_approve,
            "reject": self.cmd_reject, "pump": self.cmd_pump,
        }.items():
            app.add_handler(CommandHandler(name, fn))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        jq = app.job_queue
        jq.run_repeating(self.job_minute, interval=60, first=20)
        jq.run_repeating(self.job_pump, interval=900, first=120)
        jq.run_repeating(self.job_hourly, interval=3600, first=30)
        app.run_polling(allowed_updates=Update.ALL_TYPES)
