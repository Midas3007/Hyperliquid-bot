"""Market data: Hyperliquid REST/websocket plus free external sources.

Hyperliquid stand-ins for Bybit/Bitget metrics (see PROTOCOL.md, "Hyperliquid mapping"):
  * buy/sell ratio  -> taker buy vs sell notional from Hyperliquid's live trade stream. N/A until
                       `flow_min_coverage_min` of trades are collected (no substitute metric is used).
  * whale L/S       -> live positions of the most profitable active traders on Hyperliquid's leaderboard;
                      big taker orders from the trade stream when too few of them hold the coin
  * volatility      -> Deribit DVOL percentile (BTC/ETH IV), 30-day realized-vol percentile otherwise (RV)
  * OI / whale trend -> snapshots recorded hourly by this bot (history builds up over time)
  * news            -> public crypto RSS feeds + a per-coin Google News RSS search (no API key)
  * macro calendar  -> ForexFactory's free weekly calendar feed (CPI / FOMC / NFP), calendar.toml as fallback
  * MMR             -> Hyperliquid margin tables (maintenance rate = 1 / (2 x max leverage of the tier))
  * fees            -> the account's own rates from the userFees endpoint

Every cache TTL is shorter than the protocol's 15-minute staleness limit, and each cached value keeps the time
it was fetched (`stamp(key)`), so the rules engine can show and check data age.
"""

from __future__ import annotations

import gc
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

import requests
from hyperliquid.info import Info
from hyperliquid.utils import constants

from . import indicators as ind
from .config import Config
from .protocol import PROTOCOL as P
from .rules.sizing import Fees
from .store import Store

log = logging.getLogger(__name__)
_last_said: dict[str, float] = {}


def quiet(key: str, msg: str, *args, every_s: float = 1800, level: int = logging.WARNING) -> None:
    """Log a recurring problem (a feed down, no internet) at most once per `every_s` instead of every fetch."""
    now = time.time()
    if now - _last_said.get(key, 0) >= every_s:
        _last_said[key] = now
        log.log(level, msg, *args)


def short(e: Exception) -> str:
    """'Failed to resolve host' instead of a 300-character urllib3 chain."""
    text = str(e)
    for marker in ("Failed to resolve", "Network is unreachable", "timed out", "Connection refused",
                   "not well-formed", "Max retries exceeded"):
        if marker in text:
            return marker
    return text[:120]

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
FNG_URL = "https://api.alternative.me/fng/?limit=1"
DERIBIT_DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://www.theblock.co/rss.xml",
    "https://bitcoinist.com/feed/",
]
GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# How each protocol event name appears in the ForexFactory feed (US, high impact)
FF_EVENTS = {"CPI": ("CPI",), "FOMC": ("FOMC", "Federal Funds Rate"), "NFP": ("Non-Farm Employment Change",)}
COIN_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "Ripple", "DOGE": "Dogecoin",
    "HYPE": "Hyperliquid", "SUI": "Sui", "AVAX": "Avalanche", "LINK": "Chainlink", "ADA": "Cardano",
    "BNB": "BNB", "LTC": "Litecoin", "ENA": "Ethena", "PEPE": "Pepe", "WIF": "dogwifhat", "TRX": "Tron",
    "DOT": "Polkadot", "NEAR": "NEAR Protocol", "APT": "Aptos", "ARB": "Arbitrum", "OP": "Optimism",
    "TON": "Toncoin", "BCH": "Bitcoin Cash", "UNI": "Uniswap", "AAVE": "Aave", "ONDO": "Ondo",
    "TIA": "Celestia", "SEI": "Sei", "INJ": "Injective", "FET": "Fetch.ai", "BONK": "Bonk",
    "TRUMP": "TRUMP", "FARTCOIN": "Fartcoin", "PUMP": "Pump.fun", "XPL": "Plasma", "WLD": "Worldcoin",
}
UA = {"User-Agent": "Mozilla/5.0 (HEVI trading bot)"}


def parse_rss(content: bytes, source: str) -> list[dict]:
    """RSS items -> [{title, summary, ts, source}]. Google News titles end in ' - Publisher'; that is kept as source."""
    out = []
    for it in ET.fromstring(content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip() or source
        if src != source and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")[:400]
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except Exception:  # noqa: BLE001
            ts = 0
        out.append({"title": title, "summary": " ".join(desc.split()), "ts": ts, "source": src})
    return out


def parse_ff_calendar(rows: list[dict], names: tuple) -> list[tuple[str, float]]:
    """ForexFactory weekly feed -> [(protocol event name, utc ts)] for high-impact US releases."""
    from datetime import datetime

    out = []
    for r in rows:
        if r.get("country") != "USD" or str(r.get("impact", "")).lower() != "high":
            continue
        title = r.get("title", "")
        name = next((n for n in names if any(k.lower() in title.lower() for k in FF_EVENTS.get(n, ()))), None)
        if not name:
            continue
        try:
            out.append((name, datetime.fromisoformat(r["date"]).timestamp()))
        except Exception:  # noqa: BLE001
            continue
    return out


def _candle(c: dict) -> dict:
    return {"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])}


def base_symbol(coin: str) -> str:
    """kPEPE -> PEPE (Hyperliquid prefixes 1000x-denominated coins with 'k')."""
    if len(coin) > 1 and coin[0] == "k" and coin[1:].isupper():
        return coin[1:]
    return coin


def is_zero_hash(h: str | None) -> bool:
    return not h or set(h[2:] if h.startswith("0x") else h) <= {"0"}


def pick_whales(rows: list[dict], dd) -> list[str]:
    """Leaderboard rows -> the `whale_accounts` most profitable active traders this month (v3.3).

    Kept: account >= whale_min_account_usd, profitable this month AND all-time, and month volume between
    whale_min_turnover and whale_max_turnover times the account (below = not trading perps; above = a market maker,
    whose positions are inventory rather than bets). Ranked by this month's profit."""
    out = []
    for r in rows:
        try:
            av = float(r.get("accountValue") or 0)
            w = {k: v for k, v in r.get("windowPerformances") or []}
            m, a = w.get("month") or {}, w.get("allTime") or {}
            mp, mv, ap = float(m.get("pnl") or 0), float(m.get("vlm") or 0), float(a.get("pnl") or 0)
            addr = r["ethAddress"]
        except (TypeError, ValueError, KeyError):
            continue
        if av < dd.whale_min_account_usd or mp <= 0 or ap <= 0:
            continue
        if dd.whale_min_turnover <= mv / av <= dd.whale_max_turnover:
            out.append((mp, addr))
    out.sort(key=lambda t: t[0], reverse=True)
    return [a for _, a in out[: dd.whale_accounts]]


class FlowCollector:
    """Rolling taker buy/sell notional per coin from the websocket trade stream."""

    def __init__(self, base_url: str, window_min: float, min_coverage_min: float, stale_min: float,
                 big_window_min: float = 240, big_floor_usd: float = 10_000):
        self.base_url = base_url
        self.window_s = window_min * 60
        self.big_window_s = big_window_min * 60
        self.big_floor_usd = big_floor_usd
        # one taker order fills against several makers -> several trades with the same hash. Fills are grouped for a
        # few seconds, and orders >= big_floor_usd are kept for the big-order (whale) reading.
        self._pending: dict[str, dict] = defaultdict(dict)   # coin -> {order key: [received, side, notional]}
        self._big: dict[str, deque] = defaultdict(deque)     # coin -> (exchange ts, side, notional)
        self.min_coverage_min = min_coverage_min
        self.stale_s = stale_min * 60
        self._info: Info | None = None
        self._trades: dict[str, deque] = defaultdict(deque)
        self._since: dict[str, float] = {}
        self._subs: dict[str, int] = {}
        self._extra: list[tuple[dict, object]] = []  # e.g. the account's fill stream
        self._last_msg = 0.0
        self._lock = threading.Lock()

    def subscribe_extra(self, sub: dict, cb) -> None:
        """Extra websocket subscription that is re-made after every reconnect."""
        self._extra.append((sub, cb))
        if self._info is not None:
            self._info.subscribe(sub, cb)

    def _on_trades(self, msg: dict) -> None:
        now = time.time()
        self._last_msg = now
        with self._lock:
            for t in msg.get("data", []):
                coin = t["coin"]
                notional = float(t["px"]) * float(t["sz"])
                ts = t["time"] / 1000
                self._trades[coin].append((ts, t["side"], notional))
                h = t.get("hash")
                key = f"tid:{t.get('tid')}" if is_zero_hash(h) else h
                p = self._pending[coin].setdefault(key, [now, t["side"], 0.0, ts])
                p[2] += notional
            self._flush(now)

    def _flush(self, now: float, settle_s: float = 5.0) -> None:
        """Move grouped orders older than settle_s out of _pending; keep the big ones. Caller holds the lock."""
        for coin, pend in self._pending.items():
            done = [k for k, v in pend.items() if now - v[0] >= settle_s]
            for k in done:
                _, side, n, ts = pend.pop(k)
                if n >= self.big_floor_usd:
                    self._big[coin].append((ts, side, n))

    def heal(self, silent_s: float = 120) -> None:
        """Reconnect the trade stream if it has gone quiet (Hyperliquid expires sockets; phones drop networks).
        Called every minute, so a dropped stream is back within ~2 minutes instead of at the next hourly job."""
        if self._info is not None and self._subs and time.time() - self._last_msg > silent_s:
            self.ensure(list(self._subs), silent_s=silent_s)

    def ensure(self, coins: list[str], silent_s: float = 300) -> None:
        stale = self._info is not None and self._subs and time.time() - self._last_msg > silent_s
        if stale:
            log.warning("trade stream silent for %.0f min - reconnecting (buy/sell data restarts its 30-min window)",
                        silent_s / 60)
            try:
                self._info.disconnect_websocket()
            except Exception:  # noqa: BLE001
                pass
            self._info, self._subs = None, {}  # _since stays: buy/sell coverage survives a reconnect
        if self._info is None:
            self._info = Info(self.base_url, skip_ws=False)
            self._last_msg = time.time()
            for sub, cb in self._extra:
                try:
                    self._info.subscribe(sub, cb)
                except Exception as e:  # noqa: BLE001
                    log.warning("subscribe %s failed: %s", sub, e)
        for coin in coins:
            if coin in self._subs:
                continue
            try:
                self._subs[coin] = self._info.subscribe({"type": "trades", "coin": coin}, self._on_trades)
                self._since.setdefault(coin, time.time())
            except Exception as e:  # noqa: BLE001
                log.warning("subscribe %s failed: %s", coin, e)

    def ratio(self, coin: str) -> dict | None:
        """Taker buy/sell notional ratio over the window. Hyperliquid trade side 'B' = aggressive buy."""
        since = self._since.get(coin)
        if not since:
            return None
        cutoff = time.time() - self.window_s
        with self._lock:
            dq = self._trades[coin]
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            buy = sum(n for _, s, n in dq if s == "B")
            sell = sum(n for _, s, n in dq if s != "B")
        covered_min = min(self.window_s, time.time() - since) / 60
        if covered_min < self.min_coverage_min or buy + sell <= 0 or time.time() - self._last_msg > self.stale_s:
            return None
        return {"ratio": buy / sell if sell > 0 else 99.0, "buy_usd": buy, "sell_usd": sell,
                "minutes": round(covered_min), "source": "Hyperliquid taker trades", "ts": self._last_msg}

    def big_orders(self, coin: str, threshold_usd: float) -> dict | None:
        """Big taker orders (fills grouped by order) over the big-order window: buy vs sell notional of orders
        >= threshold_usd. None until min_coverage_min of trades or if the stream is stale."""
        since = self._since.get(coin)
        if not since:
            return None
        now = time.time()
        threshold_usd = max(threshold_usd, self.big_floor_usd)
        with self._lock:
            self._flush(now)
            dq = self._big[coin]
            while dq and dq[0][0] < now - self.big_window_s:
                dq.popleft()
            rows = [(s, n) for _, s, n in dq if n >= threshold_usd]
        covered_min = min(self.big_window_s, now - since) / 60
        if covered_min < self.min_coverage_min or now - self._last_msg > self.stale_s:
            return None
        buy = sum(n for s, n in rows if s == "B")
        sell = sum(n for s, n in rows if s != "B")
        ratio = buy / sell if sell > 0 else (99.0 if buy > 0 else None)
        return {"orders": len(rows), "buy_usd": buy, "sell_usd": sell, "ratio": ratio, "threshold_usd": threshold_usd,
                "minutes": round(covered_min), "source": "big taker orders", "ts": self._last_msg}


class Market:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.base_url = constants.MAINNET_API_URL if cfg.is_mainnet else constants.TESTNET_API_URL
        self.info = Info(self.base_url, skip_ws=True)
        self.flow = FlowCollector(self.base_url, P.screener.flow_window_min, P.screener.flow_min_coverage_min,
                                  P.data.stale_after_min, P.deep_dive.big_trade_window_min, P.deep_dive.big_trade_min_usd)
        self._cache: dict[str, tuple[float, object]] = {}
        self._http = requests.Session()
        self._http.headers.update(UA)

    # -- small TTL cache ---------------------------------------------------
    def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self._cache[key] = (time.time(), val)
        return val

    def stamp(self, key: str) -> float | None:
        """When the cached value under `key` was fetched."""
        hit = self._cache.get(key)
        return hit[0] if hit else None

    # -- Hyperliquid -------------------------------------------------------
    def ctxs(self) -> dict[str, dict]:
        def load():
            meta, ctxs = self.info.meta_and_asset_ctxs()
            tables = {}
            for tid, tbl in meta.get("marginTables", []):
                tiers = [(float(t["lowerBound"]), int(t["maxLeverage"])) for t in tbl.get("marginTiers", [])]
                if tiers:
                    tables[tid] = tiers
            out = {}
            for asset, (m, c) in enumerate(zip(meta["universe"], ctxs)):
                if m.get("isDelisted"):
                    continue
                mark = float(c.get("markPx") or 0)
                prev = float(c.get("prevDayPx") or 0)
                if mark <= 0:
                    continue
                f1h = float(c.get("funding") or 0)
                out[m["name"]] = {
                    "coin": m["name"],
                    "asset": asset,
                    "sz_decimals": int(m["szDecimals"]),
                    "max_leverage": int(m.get("maxLeverage", 1)),
                    "mark": mark,
                    "mid": float(c.get("midPx") or mark),
                    "prev_day": prev,
                    "change_pct": (mark / prev - 1) * 100 if prev else 0.0,
                    "volume_usd": float(c.get("dayNtlVlm") or 0),
                    "oi_usd": float(c.get("openInterest") or 0) * mark,
                    "funding_1h_pct": f1h * 100,
                    "funding_8h_pct": f1h * 8 * 100,
                    "margin_tiers": tables.get(m.get("marginTableId"), [(0.0, int(m.get("maxLeverage", 1)))]),
                    "only_isolated": bool(m.get("onlyIsolated", False)),
                }
            return out

        return self._cached("ctxs", 15, load)

    def candles(self, coin: str, interval: str, n: int) -> list[dict]:
        def load():
            end = int(time.time() * 1000)
            start = end - INTERVAL_MS[interval] * n
            return [_candle(c) for c in self.info.candles_snapshot(coin, interval, start, end)]

        ttl = 30 if interval in ("1m", "5m", "15m") else 300
        return self._cached(f"c:{coin}:{interval}:{n}", ttl, load)

    def candles_range(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
        """Uncached candles between two timestamps (used by paper trading to replay price since the last check)."""
        return [_candle(c) for c in self.info.candles_snapshot(coin, interval, int(start_ms), int(end_ms))]

    def buy_sell(self, coin: str) -> dict | None:
        return self.flow.ratio(coin)

    def big_orders(self, coin: str, volume_usd: float | None) -> dict | None:
        """Whale fallback: big taker orders. Big = max(big_trade_min_usd, big_trade_vol_pct % of 24h volume)."""
        dd = P.deep_dive
        return self.flow.big_orders(coin, max(dd.big_trade_min_usd, (volume_usd or 0) * dd.big_trade_vol_pct / 100))

    def day_open(self, coin: str) -> float | None:
        """Open of today's UTC daily candle (the protocol's "daily change" reference)."""
        now = int(time.time() * 1000)
        start = now // 86_400_000 * 86_400_000

        def load():
            rows = [_candle(c) for c in self.info.candles_snapshot(coin, "1d", start, now)]
            today = [c for c in rows if c["t"] == start]
            return today[-1]["o"] if today else None

        val = self._cached(f"open:{coin}:{start}", 86_400, load)
        if val is None:
            self._cache.pop(f"open:{coin}:{start}", None)  # retry next time instead of caching a miss
        return val

    def funding_hist(self, coin: str, hours: int = 12) -> list[float]:
        """Hourly funding rates (%) for the last `hours`, oldest first."""
        def load():
            start = int((time.time() - hours * 3600) * 1000)
            rows = self.info.funding_history(coin, start)
            return [float(r["fundingRate"]) * 100 for r in sorted(rows, key=lambda r: r["time"])]

        return self._cached(f"fh:{coin}", 300, load)

    def user_fees(self, address: str) -> Fees:
        """The account's actual maker/taker rates (fallback: protocol.toml base tier)."""
        def load():
            try:
                f = self.info.user_fees(address)
                return Fees(float(f["userAddRate"]) * 100, float(f["userCrossRate"]) * 100, P.fees.slippage_pct,
                            "your Hyperliquid fee tier", P.fees.slippage_on_tp)
            except Exception as e:  # noqa: BLE001
                quiet("fees", "userFees unavailable (%s) - using base-tier fees", short(e))
                return None

        got = self._cached(f"fees:{address}", 600, load) if address else None
        return got or Fees(P.fees.maker_pct, P.fees.taker_pct, P.fees.slippage_pct, "base-tier default",
                           P.fees.slippage_on_tp)

    def account(self, address: str) -> dict:
        st = self.info.user_state(address)
        ms = st.get("marginSummary", {})
        positions = []
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            szi = float(p["szi"])
            if szi == 0:
                continue
            positions.append({
                "coin": p["coin"],
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "entry": float(p["entryPx"]),
                "upnl": float(p.get("unrealizedPnl") or 0),
                "liq": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
                "leverage": p.get("leverage", {}).get("value"),
                "margin_used": float(p.get("marginUsed") or 0),
            })
        spot_usdc = 0.0
        try:
            for b in self.info.spot_user_state(address).get("balances", []):
                if b["coin"] == "USDC":
                    spot_usdc = float(b["total"])
        except Exception:  # noqa: BLE001
            pass
        return {
            "account_value": float(ms.get("accountValue") or 0),
            "withdrawable": float(st.get("withdrawable") or 0),
            "positions": positions,
            "spot_usdc": spot_usdc,
        }

    # -- external, free ------------------------------------------------------
    def fear_greed(self) -> dict | None:
        def load():
            try:
                d = self._http.get(FNG_URL, timeout=10).json()["data"][0]
                return {"value": int(d["value"]), "label": d["value_classification"]}
            except Exception as e:  # noqa: BLE001
                quiet("fng", "Fear & Greed unavailable: %s", short(e))
                return None

        return self._cached("fng", 600, load)

    def dvol(self, currency: str) -> dict | None:
        def load():
            end = int(time.time() * 1000)
            start = end - 30 * 86_400_000
            try:
                r = self._http.get(
                    DERIBIT_DVOL_URL,
                    params={"currency": currency, "start_timestamp": start, "end_timestamp": end, "resolution": "3600"},
                    timeout=10,
                ).json()
                closes = [row[4] for row in r["result"]["data"]]
                if len(closes) < 48:
                    return None
                cur = closes[-1]
                rank = ind.percentile_rank(closes, cur)
                day_ago = closes[-25] if len(closes) > 25 else closes[0]
                rank_ago = ind.percentile_rank(closes[:-24], day_ago) if len(closes) > 48 else None
                return {"dvol": cur, "rank": rank, "rank_24h_ago": rank_ago, "change_24h": cur - day_ago,
                        "source": f"Deribit DVOL ({currency}) IV percentile"}
            except Exception as e:  # noqa: BLE001
                quiet(f"dvol:{currency}", "Deribit DVOL %s unavailable: %s", currency, short(e))
                return None

        return self._cached(f"dvol:{currency}", 600, load)

    def whale_list(self) -> list[str]:
        """Addresses of the tracked traders (pick_whales), re-picked every whale_list_refresh_h hours. The leaderboard
        is a ~40 MB download, so a failed refresh keeps the previous list and retries in 10 minutes."""
        dd = P.deep_dive
        now = time.time()
        hit = self._cache.get("whale_list")
        if hit and now - hit[0] < (dd.whale_list_refresh_h * 3600 if hit[1] else 600):
            return hit[1]
        try:
            rows = self._http.get(LEADERBOARD_URL, timeout=90).json()["leaderboardRows"]
            addrs = pick_whales(rows, dd)
            del rows
            gc.collect()  # ~170 MB of parsed JSON; hand it back before the next scan
        except Exception as e:  # noqa: BLE001
            quiet("leaderboard", "whale leaderboard unavailable: %s", short(e))
            addrs = []
        if not addrs and hit and hit[1]:
            self._cache["whale_list"] = (now - dd.whale_list_refresh_h * 3600 + 600, hit[1])
            return hit[1]
        if addrs:
            log.info("whale list: %d profitable active traders tracked", len(addrs))
        self._cache["whale_list"] = (now, addrs)
        return addrs

    def whales(self) -> dict[str, dict]:
        """Aggregate live positions of the tracked traders -> per-coin long/short notional. {} = unavailable."""

        def load():
            addrs = self.whale_list()
            if not addrs:
                return {}
            def fetch(a):
                try:
                    return self.info.user_state(a).get("assetPositions", [])
                except Exception:  # noqa: BLE001
                    return None

            agg: dict[str, dict] = defaultdict(lambda: {"long_usd": 0.0, "short_usd": 0.0, "longs": 0, "shorts": 0})
            active = fails = 0
            with ThreadPoolExecutor(max_workers=4) as ex:  # ~400 request weight per refresh; the limit is 1200/min
                for aps in ex.map(fetch, addrs):
                    if aps is None:
                        fails += 1
                        continue
                    active += bool(aps)
                    for ap in aps:
                        p = ap["position"]
                        szi = float(p["szi"])
                        val = abs(float(p.get("positionValue") or 0))
                        a = agg[p["coin"]]
                        if szi > 0:
                            a["long_usd"] += val
                            a["longs"] += 1
                        elif szi < 0:
                            a["short_usd"] += val
                            a["shorts"] += 1
            if fails > len(addrs) // 4:  # a partial picture would skew the ratios: treat as missing data
                quiet("whales", "whale positions unavailable: %d of %d accounts failed", fails, len(addrs))
                return {}
            out = {}
            for coin, a in agg.items():
                ls = a["long_usd"] / a["short_usd"] if a["short_usd"] > 0 else (99.0 if a["long_usd"] > 0 else None)
                out[coin] = {**a, "ratio": ls, "holders": a["longs"] + a["shorts"], "accounts_scanned": len(addrs),
                             "accounts_active": active}
            return out

        return self._cached("whales", self.cfg.whale_cache_min * 60, load)

    def news(self, coin: str, limit: int = 5) -> list[dict]:
        """Latest headlines about `coin`, newest first: general crypto RSS feeds plus a Google News search."""
        def load_all():
            items, down = [], []
            for url in RSS_FEEDS:
                try:
                    items += parse_rss(self._http.get(url, timeout=10).content, url.split("/")[2])
                except Exception as e:  # noqa: BLE001
                    down.append(url.split("/")[2])
                    quiet(f"rss:{url}", "news feed %s skipped: %s", url.split("/")[2], short(e), level=logging.INFO)
            if len(down) == len(RSS_FEEDS):
                quiet("rss:all", "all news feeds unreachable - is the phone online?")
            return items

        sym = base_symbol(coin)
        name = COIN_NAMES.get(sym)

        def load_google():
            q = f'"{name}" crypto when:2d' if name else f'"{sym}" crypto when:2d'
            try:
                r = self._http.get(GOOGLE_NEWS_URL, params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                                   timeout=10)
                return parse_rss(r.content, "news.google.com")
            except Exception as e:  # noqa: BLE001
                quiet("gnews", "Google News unavailable: %s", short(e), level=logging.INFO)
                return []

        pat = re.compile(rf"\b{re.escape(sym)}\b" + (rf"|\b{re.escape(name)}\b" if name else ""), re.I if name else 0)
        pool = self._cached("rss", 600, load_all) + self._cached(f"gnews:{coin}", 600, load_google)
        seen, out = set(), []
        for i in sorted(pool, key=lambda x: x["ts"], reverse=True):
            key = re.sub(r"\W+", " ", i["title"].lower()).strip()
            if key in seen or not (pat.search(i["title"]) or pat.search(i["summary"])):
                continue
            seen.add(key)
            out.append(i)
        return [{"title": i["title"], "summary": i["summary"][:200], "age_h": round((time.time() - i["ts"]) / 3600, 1),
                 "source": i["source"]} for i in out[:limit]]

    def macro_calendar(self, names: tuple) -> tuple[list[tuple[str, float]], float | None]:
        """This week's high-impact US releases from ForexFactory's free feed, and how far that covers.
        Entries are Mon-Thu and every hold ends by Friday 20:00 UTC, so this week's calendar covers any hold."""
        def load():
            try:
                rows = self._http.get(FF_CALENDAR_URL, timeout=15).json()
            except Exception as e:  # noqa: BLE001
                quiet("ffcal", "macro calendar feed unavailable (%s) - using calendar.toml", short(e))
                return None
            if not isinstance(rows, list) or not rows:
                return None
            return parse_ff_calendar(rows, names), rows

        got = self._cached("ffcal", 3600, load)
        if got is None:
            self._cache.pop("ffcal", None)
            return [], None
        events, rows = got
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        friday_flat = monday + timedelta(days=4, hours=20)
        dates = []
        for r in rows:
            try:
                dates.append(datetime.fromisoformat(r["date"]).astimezone(timezone.utc))
            except Exception:  # noqa: BLE001
                pass
        # the feed must actually be this week's (not a stale copy) to count as coverage
        current = any(monday - timedelta(days=1) <= d <= friday_flat for d in dates) and now.weekday() < 5
        return events, (friday_flat.timestamp() if current else None)

    # -- OI history ----------------------------------------------------------
    def record_oi(self) -> None:
        self.store.add_oi([(c, d["oi_usd"], d["mark"]) for c, d in self.ctxs().items() if d["volume_usd"] > 1_000_000])
