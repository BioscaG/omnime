"""Structured logging configuration."""
from __future__ import annotations

import logging
import sys

from src.config import settings


def configure_logging(level: str | None = None) -> None:
    """Force-install our handler on root, replacing whatever else attached.

    Chromadb / alembic / onnxruntime each install handlers at import time;
    `logging.basicConfig` is a no-op when handlers already exist. We rebuild
    handlers from scratch to guarantee our format and the INFO threshold."""
    lvl = (level or settings.log_level).upper()
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)
    root.setLevel(lvl)

    for noisy in ("httpx", "asyncio", "chromadb", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("telegram.ext").setLevel(logging.INFO)
