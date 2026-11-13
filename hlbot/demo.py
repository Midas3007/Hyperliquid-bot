"""One-day demo session (protocol.toml [demo], v3.3, owner's request on Fri 13 Nov 2026).

A second engine on a separate $100 paper account (DATA_DIR/demo/), sharing HEVI's market data. Looser entry rules
(protocol.toml [demo]): the whale/OI/24h/news/direction filters don't block, net R/R needs 1:1.5 instead of 1:3, the
target may reach 2x ADR, 2 of 4 methods make a 5m entry, up to 4 entries, no breakers. The stop beyond 4H structure,
isolated sizing, the liquidation buffer, the BTC gate, EXTREME volatility, missing price data and the Friday 20:00 UTC
flatten all still apply.

Its trades never touch the real paper journal, so they can't count toward the go-live gate, the review or the
breakers. Every message is prefixed [DEMO], cards say "Protocol v3.3-demo", and the record page shows them on their
own DEMO tab. After `entries_until` it places nothing; after that Friday's flatten the session switches itself off.
"""

from __future__ import annotations

import dataclasses
import os
import time
from datetime import datetime

from .engine import Engine
from .protocol import Protocol
from .rules import schedule as sch

TAG = "[DEMO] "


def entries_until(P: Protocol) -> float:
    return datetime.fromisoformat(P.demo.entries_until.replace("Z", "+00:00")).timestamp()


def session_end(P: Protocol) -> float:
    """The Friday flatten after entries_until: by then every demo position is closed."""
    return sch.next_friday_flat(entries_until(P) - 1, P.schedule)


def demo_protocol(P: Protocol) -> Protocol:
    r, d = dataclasses.replace, P.demo
    cancel_hour = datetime.fromisoformat(d.entries_until.replace("Z", "+00:00")).hour
    return r(
        P,
        version=P.version + "-demo",
        schedule=r(P.schedule, entry_weekdays=tuple(range(7)), friday_cancel_hour=cancel_hour),
        filters=r(P.filters, min_net_rr=d.min_net_rr),
        entry=r(P.entry, min_methods=d.entry_min_methods),
        tp=r(P.tp, max_adr_mult=d.max_adr_mult),
        breakers=r(P.breakers, max_entries_per_day=d.max_entries, half_band_max_entries=d.max_entries),
        # no demo daily report; 5-min warm-up (the demo ignores the flow filters the normal 30-min warm-up waits for)
        autopilot=r(P.autopilot, scan_every_min=d.scan_every_min, report_hour_utc=99, warmup_min=5),
        strategy=r(P.strategy, swing=r(P.strategy.swing, max_adr_mult=d.max_adr_mult),
                   day=r(P.strategy.day, max_adr_mult=d.max_adr_mult)),
    )


class DemoEngine(Engine):
    relax_filters = True

    def __init__(self, main: Engine):  # deliberately not Engine.__init__: shares main's market data and analyst
        self.cfg = dataclasses.replace(main.cfg, mode="paper", data_dir=os.path.join(main.cfg.data_dir, "demo"))
        os.makedirs(self.cfg.data_dir, exist_ok=True)
        self.P = demo_protocol(main.P)
        self.mkt, self.analyst = main.mkt, main.analyst
        self.until = entries_until(main.P)
        self.ends = session_end(main.P)
        self._set_trader(False)
        self._init_state()
        self._started = main._started

    def label(self) -> str:
        return "DEMO - looser entry rules on a separate paper account (not counted toward going live)"

    def go_live_status(self) -> str:
        return "demo session: not counted toward going live"

    def _exits(self):
        return []  # no breakers in the demo session

    def autopilot_tick(self) -> list[str]:
        if time.time() >= self.until:
            return []
        return [TAG + m.replace("every rule passed", "the demo's looser rules passed") for m in super().autopilot_tick()]

    def manage(self) -> list[str]:
        return [TAG + m for m in super().manage()]

    def sync(self) -> list[str]:
        return [TAG + m for m in super().sync()]

    @property
    def busy(self) -> bool:
        return bool(self.store.trades("open") or self.store.trades("pending"))

    def over(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.ends and not self.busy


def make(main: Engine, now: float | None = None) -> DemoEngine | None:
    """The demo engine while its window is open, else None (so after that Friday this costs nothing)."""
    now = time.time() if now is None else now
    if now >= session_end(main.P):
        return None
    return DemoEngine(main)
