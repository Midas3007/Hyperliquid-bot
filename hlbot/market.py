"""Market data: Hyperliquid REST/websocket plus free external sources.

Free stand-ins for Bybit-only metrics:
  * buy/sell ratio  -> taker buy vs sell notional from Hyperliquid's live trade stream
                       (falls back to order-book depth imbalance until enough trades are collected)
  * whale L/S       -> live positions of the top accounts on Hyperliquid's public leaderboard
  * IV Rank         -> Deribit DVOL percentile (BTC/ETH), realized-volatility percentile otherwise
  * OI trend        -> snapshots recorded hourly by this bot (history builds up over time)
  * news            -> public RSS feeds filtered by coin
"""

from __future__ import annotations

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
from .store import Store

log = logging.getLogger(__name__)

INTERVAL_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
FNG_URL = "https://api.alternative.me/fng/?limit=1"
DERIBIT_DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
]
COIN_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "Ripple", "DOGE": "Dogecoin",
    "HYPE": "Hyperliquid", "SUI": "Sui", "AVAX": "Avalanche", "LINK": "Chainlink", "ADA": "Cardano",
    "BNB": "BNB", "LTC": "Litecoin", "ENA": "Ethena", "PEPE": "Pepe", "WIF": "dogwifhat", "TRX": "Tron",
    "DOT": "Polkadot", "NEAR": "NEAR Protocol", "APT": "Aptos", "ARB": "Arbitrum", "OP": "Optimism",
    "TON": "Toncoin", "BCH": "Bitcoin Cash", "UNI": "Uniswap", "AAVE": "Aave", "ONDO": "Ondo",
    "TIA": "Celestia", "SEI": "Sei", "INJ": "Injective", "FET": "Fetch.ai", "BONK": "Bonk",
    "TRUMP": "TRUMP", "FARTCOIN": "Fartcoin", "PUMP": "Pump.fun", "XPL": "Plasma", "WLD": "Worldcoin",
}
UA = {"User-Agent": "Mozilla/5.0 (hl-compounding-bot)"}


def _candle(c: dict) -> dict:
    return {"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])}


def base_symbol(coin: str) -> str:
    """kPEPE -> PEPE (Hyperliquid prefixes 1000x-denominated coins with 'k')."""
    if len(coin) > 1 and coin[0] == "k" and coin[1:].isupper():
        return coin[1:]
    return coin


class FlowCollector:
    """Rolling taker buy/sell notional per coin from the websocket trade stream."""

    def __init__(self, base_url: str, window_min: int):
        self.base_url = base_url
        self.window_s = window_min * 60
        self._info: Info | None = None
        self._trades: dict[str, deque] = defaultdict(deque)
        self._since: dict[str, float] = {}
        self._subs: dict[str, int] = {}
        self._last_msg = 0.0
        self._lock = threading.Lock()

    def _on_trades(self, msg: dict) -> None:
        now = time.time()
        self._last_msg = now
        with self._lock:
            for t in msg.get("data", []):
                coin = t["coin"]
                notional = float(t["px"]) * float(t["sz"])
                self._trades[coin].append((t["time"] / 1000, t["side"], notional))

    def ensure(self, coins: list[str]) -> None:
        stale = self._info is not None and self._subs and time.time() - self._last_msg > 300
        if stale:
            log.warning("trade stream silent for 5 min - reconnecting")
            try:
                self._info.disconnect_websocket()
            except Exception:  # noqa: BLE001
                pass
            self._info, self._subs, self._since = None, {}, {}
        if self._info is None:
            self._info = Info(self.base_url, skip_ws=False)
            self._last_msg = time.time()
        for coin in coins:
            if coin in self._subs:
                continue
            try:
                self._subs[coin] = self._info.subscribe({"type": "trades", "coin": coin}, self._on_trades)
                self._since[coin] = time.time()
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
        if covered_min < 10 or buy + sell <= 0:
            return None
        return {"ratio": buy / sell if sell > 0 else 99.0, "buy_usd": buy, "sell_usd": sell,
                "minutes": round(covered_min), "source": "taker trades"}


class Market:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.base_url = constants.MAINNET_API_URL if cfg.is_mainnet else constants.TESTNET_API_URL
        self.info = Info(self.base_url, skip_ws=True)
        self.flow = FlowCollector(self.base_url, cfg.flow_window_min)
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

    # -- Hyperliquid -------------------------------------------------------
    def ctxs(self) -> dict[str, dict]:
        def load():
            meta, ctxs = self.info.meta_and_asset_ctxs()
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
                }
            return out

        return self._cached("ctxs", 15, load)

    def candles(self, coin: str, interval: str, n: int) -> list[dict]:
        def load():
            end = int(time.time() * 1000)
            start = end - INTERVAL_MS[interval] * n
            return [_candle(c) for c in self.info.candles_snapshot(coin, interval, start, end)]

        ttl = 30 if interval in ("5m", "15m") else 300
        return self._cached(f"c:{coin}:{interval}:{n}", ttl, load)

    def book_imbalance(self, coin: str, depth_pct: float = 1.0) -> dict | None:
        try:
            book = self.info.l2_snapshot(coin)
        except Exception:  # noqa: BLE001
            return None
        bids, asks = book["levels"]
        if not bids or not asks:
            return None
        mid = (float(bids[0]["px"]) + float(asks[0]["px"])) / 2
        lo, hi = mid * (1 - depth_pct / 100), mid * (1 + depth_pct / 100)
        b = sum(float(x["px"]) * float(x["sz"]) for x in bids if float(x["px"]) >= lo)
        a = sum(float(x["px"]) * float(x["sz"]) for x in asks if float(x["px"]) <= hi)
        if a <= 0:
            return None
        return {"ratio": b / a, "buy_usd": b, "sell_usd": a, "minutes": 0, "source": f"book depth +/-{depth_pct:g}%"}

    def buy_sell(self, coin: str) -> dict | None:
        return self.flow.ratio(coin) or self.book_imbalance(coin)

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
                log.warning("fear&greed: %s", e)
                return None

        return self._cached("fng", 1800, load)

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
                return {"dvol": cur, "rank": rank, "label": ind.rank_label(rank), "change_24h": cur - day_ago,
                        "source": f"Deribit DVOL ({currency})"}
            except Exception as e:  # noqa: BLE001
                log.warning("dvol %s: %s", currency, e)
                return None

        return self._cached(f"dvol:{currency}", 1800, load)

    def iv(self, coin: str) -> dict | None:
        if coin in ("BTC", "ETH"):
            d = self.dvol(coin)
            if d:
                return d
        rv = ind.realized_vol_rank(self.candles(coin, "1h", 24 * 30))
        if rv:
            rv["source"] = "realized-vol percentile (30d)"
        return rv

    def whales(self) -> dict[str, dict]:
        """Aggregate live positions of the largest leaderboard accounts -> per-coin long/short notional."""

        def load():
            try:
                rows = self._http.get(LEADERBOARD_URL, timeout=30).json()["leaderboardRows"]
            except Exception as e:  # noqa: BLE001
                log.warning("leaderboard: %s", e)
                return {}
            rows = sorted(rows, key=lambda r: float(r.get("accountValue") or 0), reverse=True)
            addrs = [r["ethAddress"] for r in rows[: self.cfg.whale_accounts]]

            def fetch(a):
                try:
                    return self.info.user_state(a).get("assetPositions", [])
                except Exception:  # noqa: BLE001
                    return []

            agg: dict[str, dict] = defaultdict(lambda: {"long_usd": 0.0, "short_usd": 0.0, "longs": 0, "shorts": 0})
            with ThreadPoolExecutor(max_workers=6) as ex:
                for aps in ex.map(fetch, addrs):
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
            out = {}
            for coin, a in agg.items():
                ls = a["long_usd"] / a["short_usd"] if a["short_usd"] > 0 else (99.0 if a["long_usd"] > 0 else None)
                out[coin] = {**a, "ratio": ls, "accounts_scanned": len(addrs)}
            return out

        return self._cached("whales", self.cfg.whale_cache_min * 60, load)

    def news(self, coin: str, limit: int = 5) -> list[dict]:
        def load_all():
            items = []
            for url in RSS_FEEDS:
                try:
                    root = ET.fromstring(self._http.get(url, timeout=10).content)
                except Exception as e:  # noqa: BLE001
                    log.info("rss %s: %s", url, e)
                    continue
                for it in root.iter("item"):
                    title = (it.findtext("title") or "").strip()
                    desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")[:400]
                    try:
                        ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
                    except Exception:  # noqa: BLE001
                        ts = 0
                    items.append({"title": title, "summary": " ".join(desc.split()), "ts": ts, "source": url.split("/")[2]})
            items.sort(key=lambda x: x["ts"], reverse=True)
            return items

        sym = base_symbol(coin)
        name = COIN_NAMES.get(sym)
        pat = re.compile(rf"\b{re.escape(sym)}\b" + (rf"|\b{re.escape(name)}\b" if name else ""), re.I if name else 0)
        out = [i for i in self._cached("rss", 900, load_all) if pat.search(i["title"]) or pat.search(i["summary"])]
        return [{"title": i["title"], "summary": i["summary"][:200], "age_h": round((time.time() - i["ts"]) / 3600, 1),
                 "source": i["source"]} for i in out[:limit]]

    # -- OI history ----------------------------------------------------------
    def record_oi(self) -> None:
        self.store.add_oi([(c, d["oi_usd"], d["mark"]) for c, d in self.ctxs().items() if d["volume_usd"] > 1_000_000])

    def oi_trend(self, coin: str, oi_now: float) -> dict:
        out: dict = {"oi_usd": oi_now}
        d1 = self.store.oi_at(coin, 86400)
        if d1 and d1["oi_usd"] > 0:
            out["delta_24h_pct"] = (oi_now / d1["oi_usd"] - 1) * 100
        old = self.store.oi_oldest(coin)
        if old and old["oi_usd"] > 0:
            days = (time.time() - old["ts"]) / 86400
            if days >= 1:
                out[f"growth_{min(30, round(days))}d_pct"] = (oi_now / old["oi_usd"] - 1) * 100
        if len(out) == 1:
            out["note"] = "history still building (bot records OI hourly)"
        return out
