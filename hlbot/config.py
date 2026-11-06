"""Runtime settings from environment variables (see .env.example).

Protocol thresholds are NOT here: every rules-engine number lives in protocol.toml (hlbot/protocol.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

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
    # --- credentials (never logged) ------------------------------------------
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    hl_account_address: str = field(default_factory=lambda: os.getenv("HL_ACCOUNT_ADDRESS", ""))
    hl_api_private_key: str = field(default_factory=lambda: os.getenv("HL_API_PRIVATE_KEY", ""))
    network: str = field(default_factory=lambda: os.getenv("HL_NETWORK", "mainnet").lower())
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    anthropic_effort: str = field(default_factory=lambda: os.getenv("ANTHROPIC_EFFORT", "high"))

    # --- paper trading: live prices, simulated fills, no real orders --------
    paper: bool = field(default_factory=lambda: _b("PAPER_TRADING", False))
    paper_balance: float = field(default_factory=lambda: _f("PAPER_BALANCE", 100.0))

    # --- runtime -------------------------------------------------------------
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "./data"))
    protocol_path: str = field(default_factory=lambda: os.getenv("PROTOCOL_FILE", ""))
    calendar_path: str = field(default_factory=lambda: os.getenv("CALENDAR_FILE", ""))
    whale_cache_min: int = field(default_factory=lambda: min(_i("WHALE_CACHE_MIN", 10), 14))  # < 15 min stale rule

    @property
    def is_mainnet(self) -> bool:
        return self.network != "testnet"

    @property
    def network_label(self) -> str:
        net = "mainnet" if self.is_mainnet else "testnet"
        if self.paper:
            return f"PAPER (simulated fills on live {net} prices, no real money)"
        return "MAINNET (real money)" if self.is_mainnet else "testnet"

    def missing(self) -> list[str]:
        out = []
        required = [("TELEGRAM_BOT_TOKEN", self.telegram_token)]
        if not self.paper:  # paper trading needs no wallet
            required += [("HL_ACCOUNT_ADDRESS", self.hl_account_address),
                         ("HL_API_PRIVATE_KEY", self.hl_api_private_key)]
        for name, val in required:
            if not val:
                out.append(name)
        if not os.getenv("ANTHROPIC_API_KEY"):
            out.append("ANTHROPIC_API_KEY")
        return out


CFG = Config()
