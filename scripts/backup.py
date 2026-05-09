"""Dump structured data to JSON in data/backups/.

Usage:
    python -m scripts.backup
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.config import settings
from src.memory.db import session_scope
from src.memory import models as m


def run_backup() -> Path:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = settings.backups_dir / f"backup_{ts}.json"

    payload: dict[str, Any] = {}
    with session_scope() as s:
        for cls, key in [
            (m.UserProfile, "user_profile"),
            (m.Project, "projects"),
            (m.WorkExperience, "work_experience"),
            (m.Education, "education"),
            (m.Skill, "skills"),
            (m.Contact, "contacts"),
            (m.Achievement, "achievements"),
            (m.LifeEvent, "life_events"),
            (m.Idea, "ideas"),
            (m.Conversation, "conversations"),
            (m.MemorySummary, "memory_summaries"),
            (m.FileRecord, "files"),
        ]:
            rows = s.scalars(select(cls)).all()
            payload[key] = [_row_to_dict(r) for r in rows]

    path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    return path


def _row_to_dict(row) -> dict[str, Any]:
    cols = row.__table__.columns
    return {c.name: getattr(row, c.name) for c in cols}


def main() -> None:
    logging.basicConfig(level=settings.log_level)
    path = run_backup()
    print(f"Backup written: {path}")


if __name__ == "__main__":
    main()
