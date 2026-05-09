"""Structured logging configuration."""
from __future__ import annotations

import logging
import sys

from src.config import settings


def configure_logging(level: str | None = None) -> None:
    lvl = (level or settings.log_level).upper()
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(level=lvl, format=fmt, datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    # Mute noisy third-party libraries
    for noisy in ("httpx", "telegram.ext.Application", "asyncio", "chromadb", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
