"""Progressive summarisation: weekly digests + living user profile."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from src.brain.llm_client import LLMClient
from src.memory.db import session_scope
from src.memory.structured import StructuredStore

if TYPE_CHECKING:
    from src.memory import models as m


logger = logging.getLogger(__name__)


PROFILE_PROMPT = """You are building a "living profile" of a user from raw memory data.
Write a clear, third-person summary covering:
- Identity, role, current focus.
- Active and past projects (most relevant only).
- Skills with proficiency.
- Notable achievements.
- Key contacts and how they relate.
- Communication style and preferences (if known).

Be concise but specific. Avoid filler. Aim for 250-400 words.

Structured data:
{structured}

Recent activity (last 14 days):
{recent}
"""


WEEKLY_PROMPT = """Summarise the user's week based on their messages and structured updates.
Include: key events, project progress, decisions, new contacts, ideas captured.
Be concise. ~200 words.

Period: {period_start} → {period_end}

Messages and updates:
{content}
"""


PROJECT_BRIEF_PROMPT = """Write a 1-page executive brief for the following project. Use clear sections:
Overview, Role, Key Achievements, Technologies, Status, Challenges, Next Steps.

Project data:
{data}
"""


class Summarizer:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def _format_structured(self, user_id: int) -> str:
        with session_scope() as s:
            store = StructuredStore(s)
            user = s.get(__import__("src.memory.models", fromlist=["UserProfile"]).UserProfile, user_id)
            if not user:
                return "(no data)"
            lines: list[str] = []
            lines.append(f"Name: {user.name or 'unknown'}")
            if user.bio:
                lines.append(f"Bio: {user.bio}")
            if user.communication_style:
                lines.append(f"Communication style: {user.communication_style}")
            projects = store.list_projects(user_id)
            if projects:
                lines.append("\nProjects:")
                for p in projects:
                    lines.append(
                        f"  - {p.name} ({p.status}): {p.description or ''} "
                        f"[tech: {', '.join(p.technologies or [])}]"
                    )
            jobs = store.list_work_experience(user_id)
            if jobs:
                lines.append("\nWork experience:")
                for j in jobs:
                    end = j.end_date or "present"
                    lines.append(f"  - {j.role} @ {j.company} ({j.start_date} - {end})")
            edu = store.list_education(user_id)
            if edu:
                lines.append("\nEducation:")
                for e in edu:
                    lines.append(f"  - {e.degree or ''} in {e.field or ''} @ {e.institution}")
            skills = store.list_skills(user_id)
            if skills:
                lines.append("\nSkills:")
                lines.append(
                    "  " + ", ".join(f"{s.name} ({s.proficiency or 'n/a'})" for s in skills[:30])
                )
            contacts = store.list_contacts(user_id)
            if contacts:
                lines.append(f"\nContacts: {len(contacts)} total")
            return "\n".join(lines)

    def _recent_activity(self, user_id: int, days: int = 14) -> str:
        cutoff = datetime.utcnow() - timedelta(days=days)
        with session_scope() as s:
            from src.memory import models as m
            from sqlalchemy import select

            rows = s.scalars(
                select(m.Conversation)
                .where(m.Conversation.user_id == user_id)
                .where(m.Conversation.created_at >= cutoff)
                .order_by(m.Conversation.created_at)
            ).all()
            return "\n".join(f"[{r.created_at:%Y-%m-%d}] {r.role}: {r.message_text[:300]}" for r in rows)

    async def update_living_profile(self, user_id: int) -> str:
        structured = self._format_structured(user_id)
        recent = self._recent_activity(user_id, days=14)
        prompt = PROFILE_PROMPT.format(structured=structured, recent=recent or "(no recent messages)")
        profile = await self.llm.complete(
            prompt=prompt,
            system="You write concise, accurate user profiles.",
            model_tier="powerful",
            max_tokens=900,
        )
        with session_scope() as s:
            store = StructuredStore(s)
            store.update_user(user_id, living_profile=profile, bio=profile[:500])
        return profile

    async def generate_weekly_summary(self, user_id: int) -> str:
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        recent = self._recent_activity(user_id, days=7)
        if not recent:
            return "Nothing to summarise this week."
        summary = await self.llm.complete(
            prompt=WEEKLY_PROMPT.format(
                period_start=start.date().isoformat(),
                period_end=end.date().isoformat(),
                content=recent,
            ),
            model_tier="fast",
            max_tokens=600,
        )
        with session_scope() as s:
            StructuredStore(s).add_summary(user_id, start, end, summary)
        return summary

    async def generate_project_brief(self, project_id: int) -> str:
        with session_scope() as s:
            from src.memory import models as m

            project = s.get(m.Project, project_id)
            if not project:
                return "Project not found."
            data = (
                f"Name: {project.name}\n"
                f"Role: {project.role}\n"
                f"Description: {project.description}\n"
                f"Technologies: {', '.join(project.technologies or [])}\n"
                f"Achievements: {', '.join(project.key_achievements or [])}\n"
                f"Challenges: {project.challenges}\n"
                f"Status: {project.status}\n"
                f"Dates: {project.start_date} → {project.end_date or 'present'}\n"
                f"Details: {project.details}"
            )
        return await self.llm.complete(
            prompt=PROJECT_BRIEF_PROMPT.format(data=data),
            model_tier="powerful",
            max_tokens=900,
        )
