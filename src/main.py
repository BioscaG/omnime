"""OMNIME entry point."""
from __future__ import annotations

import logging

from src.bot import run
from src.config import settings
from src.memory.db import run_migrations, wait_for_database
from src.utils.logger import configure_logging


logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    settings.ensure_directories()

    logger.info("Booting OMNIME — provider=%s mode=%s", settings.llm_provider, settings.telegram_mode)
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in environment")
    if not settings.telegram_user_id:
        logger.warning("TELEGRAM_USER_ID not set — bot will reject all messages")

    wait_for_database()
    try:
        run_migrations()
        logger.info("Database migrations applied")
    except Exception as exc:
        logger.error("Migrations failed: %s", exc)
        raise

    run()


if __name__ == "__main__":
    main()
