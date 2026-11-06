"""Telegram plumbing: command parsing and resilient message delivery (no network)."""

import asyncio

from telegram.error import BadRequest, TimedOut

from hlbot import bot as bot_mod


def test_parse_commands():
    p = bot_mod.parse
    assert p("start ($14)") == ("start", 14.0) and p("start") == ("start", None)
    assert p("start ($14) margin $5")[0] == "start_margin"
    assert p("y 2") == ("yes", 2) and p("n") == ("discard", None) and p("try another") == ("resweep", None)
    assert p("How's it going?") == ("how", None) and p("risk 7%") == ("risk", 7.0)
    assert p("override: read it myself") == ("override", "read it myself") and p("hello")[0] == "unknown"


class FakeTgBot:
    def __init__(self, fail_times, exc=TimedOut):
        self.fail_times, self.exc, self.sent = fail_times, exc, []

    async def send_message(self, chat_id, text, **kw):
        if self.fail_times:
            self.fail_times -= 1
            raise self.exc("Timed out") if self.exc is TimedOut else self.exc("Chat not found")
        self.sent.append(text)


class FakeApp:
    def __init__(self, tg):
        self.bot = tg


def make_bot():
    b = bot_mod.Bot.__new__(bot_mod.Bot)
    b.chat_id = "123"
    return b


def test_push_retries_through_network_timeouts():
    tg = FakeTgBot(fail_times=2)
    ok = asyncio.run(make_bot()._push(FakeApp(tg), "HEVI daily report", delays=(0, 0, 0)))
    assert ok and tg.sent == ["HEVI daily report"]


def test_push_gives_up_quietly_instead_of_crashing():
    tg = FakeTgBot(fail_times=10)
    assert asyncio.run(make_bot()._push(FakeApp(tg), "x", delays=(0, 0, 0))) is False and tg.sent == []
    tg = FakeTgBot(fail_times=1, exc=BadRequest)
    assert asyncio.run(make_bot()._push(FakeApp(tg), "x", delays=(0, 0, 0))) is False
