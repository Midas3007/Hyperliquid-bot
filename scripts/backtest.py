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
    ap.add_argument("--publish", action="store_true", help="send the swing trades to the record page's BACKTEST tab "
                                                           "(RECORD_URL + RECORD_TOKEN or --token-file)")
    ap.add_argument("--token-file", help="read the record upload token from this file instead of RECORD_TOKEN")
    a = ap.parse_args()
    mkt = Market(Config(), Store(os.path.join(tempfile.mkdtemp(), "bt.db")))
    variants = bt.current_variants() + (bt.sweep_variants() if a.sweep else [])
    rep = bt.run(mkt.info, mkt.ctxs(), a.coins or None, a.top, variants, a.pace, log=lambda m: print(m, file=sys.stderr))
    print(bt.summarize(rep, variants))
    if a.publish:
        from hlbot.record import backtest_body, post

        url = os.getenv("RECORD_URL", "https://hevi-record-midart.vercel.app")
        token = open(a.token_file).read().strip() if a.token_file else os.getenv("RECORD_TOKEN", "")
        body = backtest_body(rep, "swing")
        post(url, token, body)
        print(f"\npublished {len(body['trades'])} backtest trades to {url} (BACKTEST tab)")


if __name__ == "__main__":
    main()
