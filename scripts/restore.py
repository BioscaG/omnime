"""Restore a JSON backup created by scripts.backup.

Usage:
    python -m scripts.restore <path/to/backup.json>
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.config import settings
from src.memory.db import session_scope
from src.memory import models as m


TABLES = [
    ("user_profile", m.UserProfile),
    ("projects", m.Project),
    ("work_experience", m.WorkExperience),
    ("education", m.Education),
    ("skills", m.Skill),
    ("contacts", m.Contact),
    ("achievements", m.Achievement),
    ("life_events", m.LifeEvent),
    ("ideas", m.Idea),
    ("conversations", m.Conversation),
    ("memory_summaries", m.MemorySummary),
    ("files", m.FileRecord),
]


def run_restore(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    inserted = 0
    with session_scope() as s:
        for key, cls in TABLES:
            for row in payload.get(key, []):
                obj = cls(**{k: v for k, v in row.items() if hasattr(cls, k)})
                s.add(obj)
                inserted += 1
    return inserted


def main() -> None:
    logging.basicConfig(level=settings.log_level)
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.restore <backup.json>")
        raise SystemExit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        raise SystemExit(1)
    n = run_restore(path)
    print(f"Restored {n} rows from {path}")


if __name__ == "__main__":
    main()
