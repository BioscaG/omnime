"""OMNIME entry point."""
from __future__ import annotations

import logging
import sys

from src.config import settings
from src.memory.db import run_migrations, wait_for_database
from src.utils.logger import configure_logging


logger = logging.getLogger(__name__)


def _stamp(msg: str) -> None:
    """Bypass logging entirely so we can see progress even if a third-party
    library has hijacked the root logger configuration."""
    print(f"[BOOT] {msg}", flush=True, file=sys.stderr)


def main() -> None:
    _stamp("entering main()")
    configure_logging()
    settings.ensure_directories()

    _stamp(f"booting provider={settings.llm_provider} mode={settings.telegram_mode}")
    logger.info("Booting OMNIME — provider=%s mode=%s", settings.llm_provider, settings.telegram_mode)
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in environment")
    if not settings.telegram_user_id:
        logger.warning("TELEGRAM_USER_ID not set — bot will reject all messages")

    _stamp("waiting for database")
    wait_for_database()
    _stamp("running migrations")
    try:
        run_migrations()
        _stamp("migrations done")
        logger.info("Database migrations applied")
    except Exception as exc:
        _stamp(f"migrations failed: {exc}")
        logger.error("Migrations failed: %s", exc)
        raise

    _stamp("re-applying logging configuration after migrations")
    # Some libraries (e.g. chromadb) import-time-mutate the root logger; reapply.
    configure_logging()

    _stamp("calling run() to start the bot")
    from src.bot import run

    run()


if __name__ == "__main__":
    main()
