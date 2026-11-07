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


def resolve_data_dir(env: dict) -> str:
    """Where HEVI keeps its journal, paper record and market history.

    DATA_DIR if set, else ./data. On Railway with a volume attached, the volume wins whenever DATA_DIR points
    somewhere else: anything outside the volume is wiped on every redeploy, which is never what you want."""
    d = env.get("DATA_DIR") or "./data"
    mount = env.get("RAILWAY_VOLUME_MOUNT_PATH")
    if mount:
        root = os.path.abspath(mount).rstrip("/") + "/"
        if not (os.path.abspath(d).rstrip("/") + "/").startswith(root):
            return mount
    return d


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

    # --- trading mode -------------------------------------------------------
    # prove (default): paper-trade on live prices until the paper record passes the go-live gate in protocol.toml,
    #                  then switch to real money automatically (needs the HL_* keys)
    # paper:           simulated fills only, forever
    # live:            real money from the first trade
    mode: str = field(default_factory=lambda: "paper" if _b("PAPER_TRADING", False)
                      else os.getenv("MODE", "prove").strip().lower())
    paper_balance: float = field(default_factory=lambda: _f("PAPER_BALANCE", 100.0))

    # --- runtime -------------------------------------------------------------
    data_dir: str = field(default_factory=lambda: resolve_data_dir(dict(os.environ)))
    protocol_path: str = field(default_factory=lambda: os.getenv("PROTOCOL_FILE", ""))
    calendar_path: str = field(default_factory=lambda: os.getenv("CALENDAR_FILE", ""))
    whale_cache_min: int = field(default_factory=lambda: min(_i("WHALE_CACHE_MIN", 10), 14))  # < 15 min stale rule

    @property
    def is_mainnet(self) -> bool:
        return self.network != "testnet"

    @property
    def paper(self) -> bool:
        return self.mode == "paper"

    @property
    def has_wallet(self) -> bool:
        return bool(self.hl_account_address and self.hl_api_private_key)

    def missing(self) -> list[str]:
        out = []
        if self.mode not in ("prove", "paper", "live"):
            out.append(f"MODE (got {self.mode!r}; use prove, paper or live)")
        required = [("TELEGRAM_BOT_TOKEN", self.telegram_token)]
        if self.mode == "live":  # prove can start without a wallet; paper never needs one
            required += [("HL_ACCOUNT_ADDRESS", self.hl_account_address),
                         ("HL_API_PRIVATE_KEY", self.hl_api_private_key)]
        for name, val in required:
            if not val:
                out.append(name)
        if not os.getenv("ANTHROPIC_API_KEY"):
            out.append("ANTHROPIC_API_KEY")
        return out


CFG = Config()
