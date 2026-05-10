"""Point-in-time queries: 'what was true on 2025-06-01?'"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from src.memory import models as m
from src.memory.db import session_scope
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


SUMMARY_PROMPT = """Reconstruct what was true about the user on {at_date}, using only the data below.

Stored snapshot:
{snapshot}

Output: a concise paragraph + a bulleted list of active projects/jobs/goals at that point."""


class TimeMachineSkill(BaseSkill):
    name = "time_machine"
    description = "Reconstruct what the user's life looked like at a past date."
    triggers = ["/timeline", "/timemachine", "time machine", "back in time", "what was true on"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m_ = message.lower()
        if m_.startswith("/timeline") or m_.startswith("/timemachine"):
            return 0.95
        if "time machine" in m_ or "what was true on" in m_:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        target = self._extract_date(message)
        if not target:
            return SkillResponse(text="Use /timeline YYYY-MM-DD or 'what was true on YYYY-MM-DD'.")

        snapshot = self._snapshot(context.user_id, target)
        if not snapshot:
            return SkillResponse(text=f"No data captured before {target}.")

        text = await self.llm.complete(
            prompt=SUMMARY_PROMPT.format(at_date=target.isoformat(), snapshot=snapshot),
            system="You reconstruct historical states from structured data only.",
            model_tier="fast",
            max_tokens=900,
        )
        return SkillResponse(text=text, metadata={"as_of": target.isoformat()})

    @staticmethod
    def _extract_date(message: str) -> date | None:
        m_ = re.search(r"(\d{4})-(\d{2})-(\d{2})", message)
        if not m_:
            return None
        try:
            return date(int(m_.group(1)), int(m_.group(2)), int(m_.group(3)))
        except ValueError:
            return None

    def _snapshot(self, user_id: int, at_date: date) -> str:
        cutoff = datetime.combine(at_date, datetime.max.time())
        with session_scope() as s:
            projects = list(s.scalars(
                select(m.Project).where(
                    m.Project.user_id == user_id,
                    m.Project.created_at <= cutoff,
                    or_(m.Project.end_date.is_(None), m.Project.end_date >= at_date),
                )
            ))
            jobs = list(s.scalars(
                select(m.WorkExperience).where(
                    m.WorkExperience.user_id == user_id,
                    m.WorkExperience.created_at <= cutoff,
                    or_(
                        m.WorkExperience.end_date.is_(None),
                        m.WorkExperience.end_date >= at_date,
                    ),
                    or_(
                        m.WorkExperience.start_date.is_(None),
                        m.WorkExperience.start_date <= at_date,
                    ),
                )
            ))
            goals = list(s.scalars(
                select(m.Goal).where(
                    m.Goal.user_id == user_id,
                    m.Goal.created_at <= cutoff,
                )
            ))
            achievements = list(s.scalars(
                select(m.Achievement).where(
                    m.Achievement.user_id == user_id,
                    m.Achievement.date <= at_date,
                )
            ))
        lines: list[str] = []
        if jobs:
            lines.append("Jobs at that date:")
            for j in jobs:
                lines.append(f"  - {j.role} @ {j.company}")
        if projects:
            lines.append("Active projects:")
            for p in projects:
                lines.append(f"  - {p.name} ({p.status})")
        if goals:
            lines.append("Goals:")
            for g in goals:
                lines.append(f"  - {g.description} (streak {g.streak})")
        if achievements:
            lines.append("Recent achievements (≤ that date):")
            for a in achievements[-5:]:
                lines.append(f"  - {a.title}")
        return "\n".join(lines)
