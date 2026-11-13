"""Public track record (v3.3): HEVI pushes its journal to a Netlify site backed by Neon (see record-site/).

Set RECORD_URL (e.g. https://hevi-record.netlify.app) and RECORD_TOKEN. Every few minutes HEVI sends the trades and
equity since the last /paperreset, plus a short status, whenever something changed (and at least every 30 minutes,
so the page can show "updated N min ago"). Only trade facts are sent: no wallet address, no keys, no balances beyond
the equity curve. The token is never logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

import requests

log = logging.getLogger(__name__)

FIELDS = ("id", "coin", "side", "strategy", "status", "exit_type", "entry", "fill_px", "exit_px", "sl", "tp",
          "result_r", "pnl", "risk_usd", "created_at", "filled_at", "closed_at", "version")
SHOWN = ("pending", "open", "closed")


def build(engine, now: float | None = None, account: str | None = None) -> dict:
    """The record as the site's /api/ingest expects it. account: "demo" for the demo session (hlbot/demo.py)."""
    st = engine.store
    since = engine._since()
    trades = []
    for t in st.trades(limit=2000):
        if t["status"] not in SHOWN or (t.get("closed_at") or t.get("created_at") or 0) < since:
            continue
        row = {k: t.get(k) for k in FIELDS}
        if row["side"] not in ("long", "short") or not row["coin"]:
            continue
        trades.append(row)
    equity = [[round(ts, 1), round(v, 4)] for ts, v in st.equity_series(since)][-5000:]
    last_eq = equity[-1][1] if equity else None
    status = {
        "protocol": engine.P.version,
        "live": bool(engine.live),
        "mode": engine.label(),
        "strategy": engine.strategy_label(),
        "go_live": engine.go_live_status(),
        "paused": bool(engine.paused),
        "reset_at": round(since, 1) if since else None,
        "equity": last_eq,
        "peak": round(st.peak_equity(since), 4) or None,
        "sent_at": round(now or time.time(), 1),
    }
    acct = account or ("live" if engine.live else "paper")
    if acct == "demo":
        status["note"] = ("One-day demo session (13 Nov 2026) on a separate paper account with looser entry rules, so "
                          "the page isn't empty. Not counted toward going live.")
    return {"account": acct, "status": status, "trades": trades, "equity": equity}


def backtest_body(rep, variant: str, risk_usd: float = 10.0, start_equity: float = 100.0) -> dict:
    """The backtest's 'trend side, one position like HEVI' trades for one variant, as the BACKTEST tab of the page.
    R is the honest unit; $ is R x risk_usd on a flat $100 account (no compounding). replace=True: a new run
    replaces the previous one."""
    from . import backtest as bt

    taken = bt.one_at_a_time([s for s in rep.setups if s.variant == variant and s.trend], per_coin=False)
    trades, equity, eq = [], [[round(rep.start / 1000, 1), start_equity]], start_equity
    for i, s in enumerate(taken, 1):
        eq += s.r * risk_usd
        trades.append({"id": i, "coin": s.coin, "side": s.side, "strategy": f"backtest {variant}", "status": "closed",
                       "exit_type": s.exit_type, "entry": s.entry, "fill_px": s.entry, "exit_px": s.exit_px,
                       "sl": s.sl, "tp": s.tp, "result_r": round(s.r, 4), "pnl": round(s.r * risk_usd, 4),
                       "risk_usd": risk_usd, "created_at": s.t / 1000, "filled_at": s.t / 1000,
                       "closed_at": s.exit_t / 1000, "version": rep_version()})
        equity.append([round(s.exit_t / 1000, 1), round(eq, 4)])
    day = lambda ms: time.strftime("%d %b %Y", time.gmtime(ms / 1000))  # noqa: E731
    status = {
        "protocol": rep_version(), "live": False, "strategy": f"backtest ({variant})",
        "mode": "BACKTEST - past prices, exit rules only",
        "note": (f"Backtest on Hyperliquid hourly prices, {day(rep.start)} to {day(rep.end)}, {len(rep.coins)} coins "
                 f"({', '.join(rep.coins)}). HEVI's real stop, target, sizing and Friday rules; entries on the side of "
                 "the last 24h move, one position at a time. HEVI's entry filters (flow, whales, OI, news) have no "
                 "history, so they aren't in it. $ = R x $10 on a $100 account."),
        "equity": round(eq, 2), "sent_at": round(time.time(), 1),
    }
    return {"account": "backtest", "replace": True, "status": status, "trades": trades, "equity": equity}


def rep_version() -> str:
    from .protocol import PROTOCOL

    return PROTOCOL.version


def post(url: str, token: str, body: dict, timeout: float = 30) -> None:
    r = requests.post(url.rstrip("/") + "/api/ingest", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")


class Publisher:
    def __init__(self, url: str, token: str, every_s: float = 1800, account: str | None = None):
        self.url = url.rstrip("/") + "/api/ingest" if url else ""
        self.token = token
        self.account = account
        self.every_s = every_s
        self._sent_hash: str | None = None
        self._sent_at = 0.0
        self._fail_logged = 0.0
        self._http = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    def maybe_push(self, engine, now: float | None = None) -> bool:
        """Push if the record changed, or every `every_s` as a heartbeat. Never raises; failures log once an hour."""
        if not self.enabled:
            return False
        now = now or time.time()
        try:
            body = build(engine, now, self.account)
            same = {**body, "status": {k: v for k, v in body["status"].items() if k != "sent_at"}}
            h = hashlib.sha256(json.dumps(same, sort_keys=True, default=str).encode()).hexdigest()
            if h == self._sent_hash and now - self._sent_at < self.every_s:
                return False
            r = self._http.post(self.url, json=body, timeout=20, headers={"Authorization": f"Bearer {self.token}"})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            if now - self._fail_logged > 3600:
                self._fail_logged = now
                log.warning("track record not published (%s) - check RECORD_URL / RECORD_TOKEN", str(e)[:160])
            return False
        if not self._sent_hash:
            log.info("track record published: %d trades, %d equity points", len(body["trades"]), len(body["equity"]))
        self._sent_hash, self._sent_at = h, now
        return True
