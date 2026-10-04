"""Settings loaded from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v not in (None, "") else default


def _i(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v not in (None, "") else default


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v in (None, ""):
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # --- credentials -------------------------------------------------------
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    hl_account_address: str = field(default_factory=lambda: os.getenv("HL_ACCOUNT_ADDRESS", ""))
    hl_api_private_key: str = field(default_factory=lambda: os.getenv("HL_API_PRIVATE_KEY", ""))
    network: str = field(default_factory=lambda: os.getenv("HL_NETWORK", "mainnet").lower())
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    anthropic_effort: str = field(default_factory=lambda: os.getenv("ANTHROPIC_EFFORT", "high"))

    # --- runtime -----------------------------------------------------------
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "./data"))
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "UTC"))

    # --- Module 1: BTC gate ------------------------------------------------
    btc_skip_below_pct: float = field(default_factory=lambda: _f("BTC_SKIP_BELOW_PCT", -4.0))
    btc_reduced_below_pct: float = field(default_factory=lambda: _f("BTC_REDUCED_BELOW_PCT", -2.0))
    btc_reduced_margin_usd: float = field(default_factory=lambda: _f("BTC_REDUCED_MARGIN_USD", 0.50))
    btc_sizeup_above_pct: float = field(default_factory=lambda: _f("BTC_SIZEUP_ABOVE_PCT", 3.0))
    btc_pump_check_pct: float = field(default_factory=lambda: _f("BTC_PUMP_CHECK_PCT", 2.0))

    # --- Module 2: screener ------------------------------------------------
    min_volume_usd: float = field(default_factory=lambda: _f("MIN_VOLUME_USD", 50_000_000))
    skip_coins: tuple = field(
        default_factory=lambda: tuple(
            c.strip().upper() for c in os.getenv("SKIP_COINS", "TAO").split(",") if c.strip()
        )
    )
    candidates: int = field(default_factory=lambda: _i("CANDIDATES", 5))
    flow_window_min: int = field(default_factory=lambda: _i("FLOW_WINDOW_MIN", 60))
    max_day_drop_pct: float = field(default_factory=lambda: _f("MAX_DAY_DROP_PCT", -8.0))

    # --- Module 4: direction thresholds ------------------------------------
    # Funding is expressed as an 8h-equivalent % so Bybit-style thresholds carry over
    # (Hyperliquid pays hourly; its baseline 0.00125%/h == 0.01%/8h).
    funding_cap_8h_pct: float = field(default_factory=lambda: _f("FUNDING_CAP_8H_PCT", 0.01))
    funding_deep_neg_8h_pct: float = field(default_factory=lambda: _f("FUNDING_DEEP_NEG_8H_PCT", -0.02))

    # --- Module 5/6: entry + risk ------------------------------------------
    min_sl_pct: float = field(default_factory=lambda: _f("MIN_SL_PCT", 1.5))
    max_sl_pct: float = field(default_factory=lambda: _f("MAX_SL_PCT", 8.0))
    max_leverage: int = field(default_factory=lambda: _i("MAX_LEVERAGE", 25))
    min_leverage_mode_a: int = field(default_factory=lambda: _i("MIN_LEVERAGE_MODE_A", 15))
    min_rr: float = field(default_factory=lambda: _f("MIN_RR", 3.0))
    friday_min_rr: float = field(default_factory=lambda: _f("FRIDAY_MIN_RR", 1.5))
    max_entry_distance_pct: float = field(default_factory=lambda: _f("MAX_ENTRY_DISTANCE_PCT", 2.0))
    confluence_tol_pct: float = field(default_factory=lambda: _f("CONFLUENCE_TOL_PCT", 0.25))
    maker_fee: float = field(default_factory=lambda: _f("MAKER_FEE", 0.00015))
    taker_fee: float = field(default_factory=lambda: _f("TAKER_FEE", 0.00045))
    min_order_usd: float = field(default_factory=lambda: _f("MIN_ORDER_USD", 10.0))

    # --- Module 7: sizing (defaults; changeable at runtime via Telegram) ----
    default_mode: str = field(default_factory=lambda: os.getenv("SIZING_MODE", "B").upper())
    mode_a_margin_pct: float = field(default_factory=lambda: _f("MODE_A_MARGIN_PCT", 80.0))
    mode_a_sizeup_margin_pct: float = field(default_factory=lambda: _f("MODE_A_SIZEUP_MARGIN_PCT", 85.0))
    mode_b_risk_pct: float = field(default_factory=lambda: _f("MODE_B_RISK_PCT", 15.0))
    mode_b_max_margin_pct: float = field(default_factory=lambda: _f("MODE_B_MAX_MARGIN_PCT", 85.0))

    # --- Module 8: trade management ----------------------------------------
    max_hold_hours: float = field(default_factory=lambda: _f("MAX_HOLD_HOURS", 48))
    entry_ttl_hours: float = field(default_factory=lambda: _f("ENTRY_TTL_HOURS", 4))
    proposal_ttl_min: float = field(default_factory=lambda: _f("PROPOSAL_TTL_MIN", 10))
    friday_no_entry_after_hour: int = field(default_factory=lambda: _i("FRIDAY_NO_ENTRY_AFTER_HOUR", 16))
    friday_alarm_hour: int = field(default_factory=lambda: _i("FRIDAY_ALARM_HOUR", 15))
    friday_close_hour: int = field(default_factory=lambda: _i("FRIDAY_CLOSE_HOUR", 20))
    friday_max_pump_pct: float = field(default_factory=lambda: _f("FRIDAY_MAX_PUMP_PCT", 5.0))

    # --- whales (free proxy: top leaderboard accounts' live positions) -----
    whale_accounts: int = field(default_factory=lambda: _i("WHALE_ACCOUNTS", 60))
    whale_cache_min: int = field(default_factory=lambda: _i("WHALE_CACHE_MIN", 30))


    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def is_mainnet(self) -> bool:
        return self.network != "testnet"

    def missing(self) -> list[str]:
        out = []
        for name, val in (
            ("TELEGRAM_BOT_TOKEN", self.telegram_token),
            ("HL_ACCOUNT_ADDRESS", self.hl_account_address),
            ("HL_API_PRIVATE_KEY", self.hl_api_private_key),
        ):
            if not val:
                out.append(name)
        if not os.getenv("ANTHROPIC_API_KEY"):
            out.append("ANTHROPIC_API_KEY")
        return out


CFG = Config()
