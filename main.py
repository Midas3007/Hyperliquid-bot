"""HEVI - autonomous Hyperliquid trading bot. Start with:  python main.py"""

import logging
import sys

from hlbot.config import CFG


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines would include the bot token
    missing = CFG.missing()
    if missing:
        sys.exit(f"HEVI can't start - missing settings: {', '.join(missing)}. Fill them in .env (see .env.example).")
    logging.getLogger("hevi").info("HEVI starting (mode=%s)", CFG.mode)
    from hlbot.bot import Bot

    Bot().run()


if __name__ == "__main__":
    main()
