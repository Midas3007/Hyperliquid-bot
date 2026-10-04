import logging
import sys

from hlbot.config import CFG


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    missing = CFG.missing()
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)} (see .env.example)")
    from hlbot.bot import Bot

    Bot().run()


if __name__ == "__main__":
    main()
