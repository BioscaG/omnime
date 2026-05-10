"""Per-domain memory for the browser agent: successful recipes + free-form notes.

Saved across sessions. Injected into the prompt at the start of every new
browse to prime the agent with what's worked / what's known about the site.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from src.memory import models as m
from src.memory.db import session_scope


logger = logging.getLogger(__name__)


def domain_of(url: str) -> str:
    if not url or url.startswith("about:"):
        return ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return ""
    # Drop www. and any trailing port — keep the recognisable site name.
    return re.sub(r"^www\.", "", host.lower())


@dataclass
class Recipe:
    id: int
    domain: str
    goal_template: str
    steps: list[dict[str, Any]]
    uses: int
    successes: int


class BrowserMemory:
    """Persistence + retrieval for browser learning."""

    @staticmethod
    def save_recipe(
        user_id: int,
        domain: str,
        goal_template: str,
        steps: list[dict[str, Any]],
    ) -> None:
        if not domain or not steps:
            return
        with session_scope() as s:
            existing = s.scalar(
                select(m.BrowserRecipe).where(
                    m.BrowserRecipe.user_id == user_id,
                    m.BrowserRecipe.domain == domain,
                    m.BrowserRecipe.goal_template == goal_template,
                )
            )
            if existing:
                existing.steps = steps
                existing.uses = (existing.uses or 0) + 1
                existing.successes = (existing.successes or 0) + 1
                existing.last_used_at = datetime.utcnow()
            else:
                s.add(m.BrowserRecipe(
                    user_id=user_id,
                    domain=domain,
                    goal_template=goal_template,
                    steps=steps,
                ))

    @staticmethod
    def find_recipes(user_id: int, domain: str, limit: int = 3) -> list[Recipe]:
        if not domain:
            return []
        with session_scope() as s:
            rows = list(s.scalars(
                select(m.BrowserRecipe)
                .where(
                    m.BrowserRecipe.user_id == user_id,
                    m.BrowserRecipe.domain == domain,
                )
                .order_by(
                    m.BrowserRecipe.successes.desc(),
                    m.BrowserRecipe.last_used_at.desc(),
                )
                .limit(limit)
            ))
            return [
                Recipe(
                    id=r.id,
                    domain=r.domain,
                    goal_template=r.goal_template,
                    steps=r.steps or [],
                    uses=r.uses or 0,
                    successes=r.successes or 0,
                )
                for r in rows
            ]

    @staticmethod
    def add_note(user_id: int, domain: str, note: str, kind: str = "observation") -> None:
        note = note.strip()
        if not (domain and note):
            return
        with session_scope() as s:
            existing = s.scalar(
                select(m.BrowserSiteNote).where(
                    m.BrowserSiteNote.user_id == user_id,
                    m.BrowserSiteNote.domain == domain,
                    m.BrowserSiteNote.note == note,
                )
            )
            if existing:
                return
            s.add(m.BrowserSiteNote(
                user_id=user_id, domain=domain, note=note, kind=kind,
            ))

    @staticmethod
    def get_notes(user_id: int, domain: str, limit: int = 8) -> list[str]:
        if not domain:
            return []
        with session_scope() as s:
            rows = list(s.scalars(
                select(m.BrowserSiteNote)
                .where(
                    m.BrowserSiteNote.user_id == user_id,
                    m.BrowserSiteNote.domain == domain,
                )
                .order_by(m.BrowserSiteNote.created_at.desc())
                .limit(limit)
            ))
            return [r.note for r in rows]


def render_priming(recipes: list[Recipe], notes: list[str]) -> str:
    """Format prior knowledge as a compact block for the LLM prompt."""
    if not recipes and not notes:
        return ""
    parts: list[str] = ["PRIOR KNOWLEDGE FROM PAST SESSIONS:"]
    for r in recipes[:3]:
        compact = json.dumps(r.steps[:8], indent=0, ensure_ascii=False)[:1200]
        parts.append(
            f"\nRecipe ({r.successes}/{r.uses} successful) for goal '{r.goal_template[:80]}':"
        )
        parts.append(compact)
    if notes:
        parts.append("\nNotes:")
        for n in notes[:8]:
            parts.append(f"- {n}")
    return "\n".join(parts)
