"""Initialize the database schema (idempotent).

Usage:
    python -m scripts.init_db
"""
from __future__ import annotations

import logging

from src.config import settings
from src.memory.db import get_engine
from src.memory.models import Base


def main() -> None:
    logging.basicConfig(level=settings.log_level)
    engine = get_engine()
    Base.metadata.create_all(engine)
    print(f"Schema created/up-to-date in {settings.database_url}")


if __name__ == "__main__":
    main()
