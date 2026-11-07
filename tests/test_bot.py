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


# ---------------------------------------------------------------- internet watchdog
def test_watchdog_pauses_work_offline_and_reports_long_outages(monkeypatch):
    b = make_bot()
    b._offline_since = None
    tg = FakeTgBot(fail_times=0)
    app = FakeApp(tg)
    clock = [1_000_000.0]
    monkeypatch.setattr(bot_mod.time, "time", lambda: clock[0])
    monkeypatch.setattr(bot_mod.Bot, "_online", staticmethod(lambda host="x": False))
    assert asyncio.run(b._net_check(app)) is False and b._offline_since == 1_000_000.0

    ran = []

    class Ctx:
        application = app

    asyncio.run(b._job(Ctx(), [lambda: ran.append(1)]))
    assert ran == []  # offline: jobs don't run (and don't spam errors)

    clock[0] += 3600
    monkeypatch.setattr(bot_mod.Bot, "_online", staticmethod(lambda host="x": True))
    assert asyncio.run(b._net_check(app)) is True and b._offline_since is None
    assert len(tg.sent) == 1 and "offline for 60 min" in tg.sent[0]

    # a short blip (< 5 min) is logged, not messaged
    monkeypatch.setattr(bot_mod.Bot, "_online", staticmethod(lambda host="x": False))
    asyncio.run(b._net_check(app))
    clock[0] += 60
    monkeypatch.setattr(bot_mod.Bot, "_online", staticmethod(lambda host="x": True))
    asyncio.run(b._net_check(app))
    assert len(tg.sent) == 1


def test_job_turns_network_errors_into_one_line(caplog):
    import requests

    b = make_bot()
    b._offline_since = None

    class Ctx:
        application = FakeApp(FakeTgBot(fail_times=0))

    def boom():
        raise requests.exceptions.ConnectionError("Max retries exceeded ... Failed to resolve 'x'")

    with caplog.at_level("WARNING"):
        asyncio.run(b._job(Ctx(), [boom]))
    assert any("network problem" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


def test_quiet_logs_a_recurring_problem_once(caplog, monkeypatch):
    from hlbot import market

    clock = [5_000.0]
    monkeypatch.setattr(market.time, "time", lambda: clock[0])
    market._last_said.clear()
    with caplog.at_level("INFO"):
        for _ in range(5):
            market.quiet("rss:x", "feed down")
        clock[0] += 1801
        market.quiet("rss:x", "feed down")
    assert sum(r.message == "feed down" for r in caplog.records) == 2
    assert market.short(Exception("HTTPSConnectionPool(...): Max retries exceeded ... Failed to resolve 'a'")) == \
        "Failed to resolve"


def test_trade_stream_heals_after_going_quiet(monkeypatch):
    from hlbot import market

    made = []

    class FakeInfo:
        def __init__(self, url, skip_ws):
            made.append(self)

        def subscribe(self, sub, cb):
            return 1

        def disconnect_websocket(self):
            pass

    monkeypatch.setattr(market, "Info", FakeInfo)
    fc = market.FlowCollector("url", 60, 30, 15)
    fc.ensure(["SOL", "ETH"])
    assert len(made) == 1 and set(fc._subs) == {"SOL", "ETH"}
    fc.heal()
    assert len(made) == 1  # still fresh
    fc._last_msg -= 200  # silent for > 2 min
    fc.heal()
    assert len(made) == 2 and set(fc._subs) == {"SOL", "ETH"}  # reconnected and resubscribed
