"""SQLite persistence: settings, the Module 11 trade and skip logs, equity history, events, hypotheses, snapshots.

Trade log (Module 11): #, date, protocol version, coin, direction, setup score, BTC band, entry, SL, TP,
risk ($ and %), exit price, exit type, result ($ and R), MAE and MFE (in R), fees and funding paid.
Skip log: coin, the rule that blocked it, and 48h later whether TP or SL would have hit first.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT, side TEXT, status TEXT,            -- pending | open | closed | cancelled
    entry REAL, sl REAL, tp REAL, qty REAL, leverage INTEGER, margin REAL,
    entry_oid INTEGER, created_at REAL, filled_at REAL, closed_at REAL,
    exit_px REAL, pnl REAL, close_reason TEXT,
    plan TEXT,                                     -- JSON: card + every input behind it
    study TEXT                                     -- JSON: post-trade study
);
CREATE TABLE IF NOT EXISTS skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, version TEXT, coin TEXT, side TEXT, rule TEXT, detail TEXT,
    entry REAL, sl REAL, tp REAL,                  -- the would-be bracket, when one was computed
    outcome TEXT, checked_at REAL                  -- tp | sl | no fill | neither | n/a (48h later)
);
CREATE TABLE IF NOT EXISTS equity (ts REAL, value REAL);
CREATE TABLE IF NOT EXISTS events (ts REAL, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT, status TEXT, source_trade INTEGER, created_at REAL   -- hypothesis | rule | rejected
);
CREATE TABLE IF NOT EXISTS oi (coin TEXT, ts REAL, oi_usd REAL, px REAL);
CREATE INDEX IF NOT EXISTS oi_coin_ts ON oi(coin, ts);
CREATE TABLE IF NOT EXISTS whale (coin TEXT, ts REAL, ratio REAL);
CREATE INDEX IF NOT EXISTS whale_coin_ts ON whale(coin, ts);
"""

# Columns added by protocol v3.0 (added to older databases on startup).
TRADE_COLUMNS_V3 = {
    "version": "TEXT", "score": "INTEGER", "btc_band": "TEXT", "risk_usd": "REAL", "risk_pct": "REAL",
    "risk_mode": "TEXT", "fill_px": "REAL", "exit_type": "TEXT", "result_r": "REAL", "mae_r": "REAL",
    "mfe_r": "REAL", "fees": "REAL", "funding": "REAL", "override": "TEXT", "bracket_r": "REAL",
}
LESSON_COLUMNS_V3 = {"support": "TEXT"}  # JSON list of trade ids that support a hypothesis

HARDCODED_LESSONS = [
    "Sub-1.5% stops were hunted repeatedly (SUI x3, Trade 11) -> 1.5% floor.",
    "TAO lost on a -2% BTC day despite a strong screener -> BTC gate.",
    "1D Bollinger R1 TPs were unrealistic for 1-2 day holds -> 4H TP rule.",
    "WLD Trade 10 and HYPE Trade 13 closed early before TP -> discretionary exits are logged against the bracket.",
    "0.01%/8h funding is the neutral default, not a cap.",
]


class Store:
    def __init__(self, data_dir: str, filename: str = "bot.db"):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, filename)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._add_columns("trades", TRADE_COLUMNS_V3)
            self._add_columns("lessons", LESSON_COLUMNS_V3)
            self._db.execute("UPDATE lessons SET status='hypothesis' WHERE status='proposed'")
            self._db.execute("UPDATE lessons SET status='rule' WHERE status='active'")
            self._db.commit()

    def _add_columns(self, table: str, cols: dict[str, str]) -> None:
        have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
        for name, typ in cols.items():
            if name not in have:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # settings ------------------------------------------------------------
    def get(self, k: str, default: Any = None) -> Any:
        rows = self._q("SELECT v FROM settings WHERE k=?", (k,))
        return json.loads(rows[0]["v"]) if rows else default

    def set(self, k: str, v: Any) -> None:
        self._x("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    # trades --------------------------------------------------------------
    def add_trade(self, **f) -> int:
        f.setdefault("created_at", time.time())
        for k in ("plan", "study"):
            if isinstance(f.get(k), (dict, list)):
                f[k] = json.dumps(f[k], default=str)
        cols = ",".join(f)
        cur = self._x(f"INSERT INTO trades({cols}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return int(cur.lastrowid)

    def update_trade(self, tid: int, **f) -> None:
        for k in ("plan", "study"):
            if isinstance(f.get(k), (dict, list)):
                f[k] = json.dumps(f[k], default=str)
        sets = ",".join(f"{k}=?" for k in f)
        self._x(f"UPDATE trades SET {sets} WHERE id=?", (*f.values(), tid))

    def trades(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            return self._q("SELECT * FROM trades WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return self._q("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))

    def trade(self, tid: int) -> dict | None:
        rows = self._q("SELECT * FROM trades WHERE id=?", (tid,))
        return rows[0] if rows else None

    def closed(self, since: float = 0.0) -> list[dict]:
        """Closed trades, oldest first (the review and breakers read these)."""
        return self._q("SELECT * FROM trades WHERE status='closed' AND closed_at>=? ORDER BY closed_at", (since,))

    def entries_since(self, ts: float) -> list[str]:
        """Coins of entry orders placed since `ts` (Module 8: max 2 new entries per UTC day)."""
        rows = self._q("SELECT coin FROM trades WHERE created_at>=? AND status!='rejected' ORDER BY id", (ts,))
        return [r["coin"] for r in rows]

    # skip log ------------------------------------------------------------
    def add_skip(self, version: str, coin: str, side: str | None, rule: str, detail: str,
                 entry: float | None = None, sl: float | None = None, tp: float | None = None) -> None:
        outcome = None if entry and sl and tp else "n/a"
        self._x("INSERT INTO skips(ts,version,coin,side,rule,detail,entry,sl,tp,outcome) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (time.time(), version, coin, side, rule, detail, entry, sl, tp, outcome))

    def skips_due(self, older_than: float) -> list[dict]:
        return self._q("SELECT * FROM skips WHERE outcome IS NULL AND ts<=? ORDER BY id LIMIT 50", (older_than,))

    def set_skip_outcome(self, sid: int, outcome: str) -> None:
        self._x("UPDATE skips SET outcome=?, checked_at=? WHERE id=?", (outcome, time.time(), sid))

    def skips(self, limit: int = 200) -> list[dict]:
        return self._q("SELECT * FROM skips ORDER BY id DESC LIMIT ?", (limit,))

    # equity --------------------------------------------------------------
    def record_equity(self, value: float) -> None:
        if value > 0:
            self._x("INSERT INTO equity(ts,value) VALUES(?,?)", (time.time(), value))

    def peak_equity(self, since: float = 0.0) -> float:
        rows = self._q("SELECT MAX(value) m FROM equity WHERE ts>=?", (since,))
        return float(rows[0]["m"] or 0.0)

    # events (breakers, overrides, mismatches, reviews) ---------------------
    def add_event(self, kind: str, detail: str) -> None:
        self._x("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", (time.time(), kind, detail))

    def last_event(self, kind: str) -> dict | None:
        rows = self._q("SELECT * FROM events WHERE kind=? ORDER BY ts DESC LIMIT 1", (kind,))
        return rows[0] if rows else None

    # hypotheses (Module 11: a lesson is a hypothesis until 5+ trades support it) ---
    def add_lesson(self, text: str, source_trade: int | None) -> int:
        cur = self._x("INSERT INTO lessons(text,status,source_trade,created_at,support) VALUES(?,?,?,?,?)",
                      (text, "hypothesis", source_trade, time.time(), json.dumps([source_trade] if source_trade else [])))
        return int(cur.lastrowid)

    def support_lesson(self, lid: int, trade_id: int) -> None:
        rows = self._q("SELECT support FROM lessons WHERE id=?", (lid,))
        if not rows:
            return
        ids = set(json.loads(rows[0]["support"] or "[]"))
        ids.add(trade_id)
        self._x("UPDATE lessons SET support=? WHERE id=?", (json.dumps(sorted(ids)), lid))

    def set_lesson(self, lid: int, status: str) -> bool:
        return self._x("UPDATE lessons SET status=? WHERE id=?", (status, lid)).rowcount > 0

    def lessons(self, status: str | None = None) -> list[dict]:
        rows = (self._q("SELECT * FROM lessons WHERE status=? ORDER BY id", (status,)) if status
                else self._q("SELECT * FROM lessons ORDER BY id"))
        for r in rows:
            r["support"] = json.loads(r.get("support") or "[]")
        return rows

    def lesson(self, lid: int) -> dict | None:
        rows = [r for r in self.lessons() if r["id"] == lid]
        return rows[0] if rows else None

    def active_lessons(self) -> list[str]:
        return HARDCODED_LESSONS + [r["text"] for r in self.lessons("rule")]

    # snapshots the bot records itself (Hyperliquid has no OI or whale history) --------
    def add_oi(self, rows: list[tuple[str, float, float]]) -> None:
        now = time.time()
        with self._lock:
            self._db.executemany("INSERT INTO oi(coin,ts,oi_usd,px) VALUES(?,?,?,?)", [(c, now, o, p) for c, o, p in rows])
            self._db.execute("DELETE FROM oi WHERE ts < ?", (now - 40 * 86400,))
            self._db.commit()

    def oi_at(self, coin: str, seconds_ago: float, tolerance_s: float = 7200) -> dict | None:
        """Closest OI snapshot at or before `seconds_ago`, if within `tolerance_s` of that point."""
        target = time.time() - seconds_ago
        rows = self._q("SELECT ts, oi_usd, px FROM oi WHERE coin=? AND ts<=? ORDER BY ts DESC LIMIT 1", (coin, target))
        if rows and target - rows[0]["ts"] < tolerance_s:
            return rows[0]
        return None

    def oi_history_hours(self) -> float:
        """How many hours of OI snapshots exist (the OI 24h filter needs a full day)."""
        rows = self._q("SELECT MIN(ts) a FROM oi")
        return (time.time() - rows[0]["a"]) / 3600 if rows and rows[0]["a"] else 0.0

    def add_whales(self, rows: list[tuple[str, float]]) -> None:
        now = time.time()
        with self._lock:
            self._db.executemany("INSERT INTO whale(coin,ts,ratio) VALUES(?,?,?)", [(c, now, r) for c, r in rows])
            self._db.execute("DELETE FROM whale WHERE ts < ?", (now - 40 * 86400,))
            self._db.commit()

    def whale_at(self, coin: str, seconds_ago: float, tolerance_s: float = 7200) -> float | None:
        target = time.time() - seconds_ago
        rows = self._q("SELECT ts, ratio FROM whale WHERE coin=? AND ts<=? ORDER BY ts DESC LIMIT 1", (coin, target))
        if rows and target - rows[0]["ts"] < tolerance_s:
            return rows[0]["ratio"]
        return None
