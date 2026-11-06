"""SQLite persistence: runtime settings, trade journal, lessons, OI history."""

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
    plan TEXT,                                     -- JSON: full card / analysis snapshot
    study TEXT                                     -- JSON: post-trade study
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT, status TEXT, source_trade INTEGER, created_at REAL   -- proposed | active | rejected
);
CREATE TABLE IF NOT EXISTS oi (coin TEXT, ts REAL, oi_usd REAL, px REAL);
CREATE INDEX IF NOT EXISTS oi_coin_ts ON oi(coin, ts);
"""

HARDCODED_LESSONS = [
    "Never enter a coin down >8% on the day regardless of whale data.",
    "SL below 1.5% gets hunted - always widen.",
    "TAO: exercise extreme caution despite bullish screener data (skipped by default).",
    "Trust TP levels - early exits leave money on the table.",
    "Never re-enter the same coin multiple times on a red day.",
    "Chasing a +5% intraday pump on Friday = high risk, avoid.",
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
            self._db.commit()

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
        if isinstance(f.get("plan"), (dict, list)):
            f["plan"] = json.dumps(f["plan"], default=str)
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

    def closed_since(self, ts: float) -> list[dict]:
        """Closed trades (oldest first) closed at or after `ts` - the input for /stats."""
        return self._q("SELECT * FROM trades WHERE status='closed' AND closed_at>=? ORDER BY closed_at", (ts,))

    def trade(self, tid: int) -> dict | None:
        rows = self._q("SELECT * FROM trades WHERE id=?", (tid,))
        return rows[0] if rows else None

    def losses_today(self, coin: str, day_start_ts: float) -> int:
        rows = self._q(
            "SELECT COUNT(*) n FROM trades WHERE coin=? AND status='closed' AND pnl<0 AND closed_at>=?",
            (coin, day_start_ts),
        )
        return rows[0]["n"]

    # lessons -------------------------------------------------------------
    def add_lesson(self, text: str, source_trade: int | None) -> int:
        cur = self._x(
            "INSERT INTO lessons(text,status,source_trade,created_at) VALUES(?,?,?,?)",
            (text, "proposed", source_trade, time.time()),
        )
        return int(cur.lastrowid)

    def set_lesson(self, lid: int, status: str) -> bool:
        return self._x("UPDATE lessons SET status=? WHERE id=?", (status, lid)).rowcount > 0

    def lessons(self, status: str | None = None) -> list[dict]:
        if status:
            return self._q("SELECT * FROM lessons WHERE status=? ORDER BY id", (status,))
        return self._q("SELECT * FROM lessons ORDER BY id")

    def active_lessons(self) -> list[str]:
        return HARDCODED_LESSONS + [r["text"] for r in self.lessons("active")]

    # open interest history -------------------------------------------------
    def add_oi(self, rows: list[tuple[str, float, float]]) -> None:
        now = time.time()
        with self._lock:
            self._db.executemany("INSERT INTO oi(coin,ts,oi_usd,px) VALUES(?,?,?,?)", [(c, now, o, p) for c, o, p in rows])
            self._db.execute("DELETE FROM oi WHERE ts < ?", (now - 40 * 86400,))
            self._db.commit()

    def oi_at(self, coin: str, seconds_ago: float) -> dict | None:
        """Closest OI snapshot at or before `seconds_ago`, if within 2h of that point."""
        target = time.time() - seconds_ago
        rows = self._q("SELECT ts, oi_usd, px FROM oi WHERE coin=? AND ts<=? ORDER BY ts DESC LIMIT 1", (coin, target))
        if rows and target - rows[0]["ts"] < 7200:
            return rows[0]
        return None

    def oi_oldest(self, coin: str) -> dict | None:
        rows = self._q("SELECT ts, oi_usd, px FROM oi WHERE coin=? ORDER BY ts ASC LIMIT 1", (coin,))
        return rows[0] if rows else None
