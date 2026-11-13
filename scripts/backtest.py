"""Replay Hyperliquid price history through HEVI's bracket rules (see hlbot/backtest.py for what it does and doesn't test).

    python scripts/backtest.py                 # the current swing and day modes, top 15 coins by volume
    python scripts/backtest.py --sweep         # also try other ADR reach limits and hold times
    python scripts/backtest.py --top 25 SOL    # more coins, or name them
"""

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hlbot import backtest as bt  # noqa: E402
from hlbot.config import Config  # noqa: E402
from hlbot.market import Market  # noqa: E402
from hlbot.store import Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("coins", nargs="*")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--pace", type=float, default=12.0, help="seconds between coins (rate limit)")
    a = ap.parse_args()
    mkt = Market(Config(), Store(os.path.join(tempfile.mkdtemp(), "bt.db")))
    variants = bt.current_variants() + (bt.sweep_variants() if a.sweep else [])
    rep = bt.run(mkt.info, mkt.ctxs(), a.coins or None, a.top, variants, a.pace, log=lambda m: print(m, file=sys.stderr))
    print(bt.summarize(rep, variants))


if __name__ == "__main__":
    main()
