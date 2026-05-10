"""Weekly review ritual: wins, stuck items, next-week goals + reflection."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


REVIEW_PROMPT = """You are running this user's weekly review.

Today: {today}
Week period: {week_start} → {week_end}

Recent activity (their messages and stored events from the past 7 days):
{activity}

Active goals + streaks:
{goals}

Living profile snapshot:
{profile}

Produce a weekly review with this exact structure as JSON:
{{
  "wins": [up to 3 short bullets, concrete],
  "stuck": [up to 3 short bullets describing where progress stalled or feels heavy],
  "goals_next_week": [up to 3 small, specific commitments for next week],
  "reflection_questions": [3 short reflective questions to send back to the user],
  "tone": "supportive but direct",
  "summary": "<2-3 sentences synthesising the week>"
}}

Output ONLY valid JSON, no preamble.
"""


class WeeklyReviewSkill(BaseSkill):
    name = "weekly_review"
    description = "Run the user's weekly review ritual: wins, stuck items, next-week goals."
    triggers = ["/review", "weekly review", "sunday review", "week recap"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/review"):
            return 0.95
        if "weekly review" in m or "week recap" in m or "sunday review" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        activity = self._activity_block(context.user_id, start, end)
        goals = self._goals_block(context.user_id)
        profile = (context.living_profile or context.profile.get("bio") or "(unknown)")[:1500]

        raw = await self.llm.complete(
            prompt=REVIEW_PROMPT.format(
                today=date.today().isoformat(),
                week_start=start.date().isoformat(),
                week_end=end.date().isoformat(),
                activity=activity or "(no activity captured)",
                goals=goals or "(no active goals)",
                profile=profile,
            ),
            system="You are a thoughtful weekly review coach.",
            model_tier="powerful",
            max_tokens=1200,
        )
        data = self._parse_json(raw)
        if not data:
            return SkillResponse(text="Couldn't generate the review (LLM returned malformed JSON).")

        with session_scope() as s:
            StructuredStore(s).add_weekly_review(
                user_id=context.user_id,
                week_start=start.date(),
                wins=data.get("wins"),
                stuck=data.get("stuck"),
                goals_next_week=data.get("goals_next_week"),
                reflection=data.get("summary"),
            )

        text = self._format(data, start, end)
        return SkillResponse(
            text=text,
            inline_buttons=[[
                {"text": "✅ Looks right", "callback_data": "review:keep"},
                {"text": "✏️ Edit", "callback_data": "review:edit"},
            ]],
            metadata={"review": data, "week_start": start.date().isoformat()},
        )

    def _activity_block(self, user_id: int, start: datetime, end: datetime) -> str:
        with session_scope() as s:
            from sqlalchemy import select
            from src.memory import models as m

            convs = list(s.scalars(
                select(m.Conversation)
                .where(m.Conversation.user_id == user_id)
                .where(m.Conversation.created_at >= start)
                .where(m.Conversation.created_at <= end)
                .order_by(m.Conversation.created_at)
            ))
            events = list(s.scalars(
                select(m.LifeEvent)
                .where(m.LifeEvent.user_id == user_id)
                .where(m.LifeEvent.date >= start.date())
            ))
            achievements = list(s.scalars(
                select(m.Achievement)
                .where(m.Achievement.user_id == user_id)
                .where(m.Achievement.date >= start.date())
            ))
        lines: list[str] = []
        for c in convs:
            lines.append(f"[{c.created_at:%Y-%m-%d}] {c.role}: {c.message_text[:200]}")
        for e in events:
            lines.append(f"EVENT [{e.date}] {e.title}: {e.description or ''}")
        for a in achievements:
            lines.append(f"WIN [{a.date}] {a.title}: {a.description or ''}")
        return "\n".join(lines)

    def _goals_block(self, user_id: int) -> str:
        with session_scope() as s:
            goals = StructuredStore(s).list_goals(user_id, status="active")
            return "\n".join(
                f"- {g.description} (streak {g.streak}, last {g.last_check_in})"
                for g in goals
            )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        s = raw.strip()
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
        if m:
            s = m.group(1)
        try:
            return json.loads(s)
        except Exception as exc:
            logger.warning("Weekly review JSON parse failed: %s", exc)
            return None

    @staticmethod
    def _format(data: dict[str, Any], start: datetime, end: datetime) -> str:
        wins = "\n".join(f"  • {w}" for w in (data.get("wins") or [])) or "  (none)"
        stuck = "\n".join(f"  • {s}" for s in (data.get("stuck") or [])) or "  (none)"
        goals = "\n".join(f"  • {g}" for g in (data.get("goals_next_week") or [])) or "  (none)"
        questions = "\n".join(f"  ? {q}" for q in (data.get("reflection_questions") or []))
        summary = data.get("summary") or ""
        return (
            f"📅 Weekly review {start.date()} → {end.date()}\n\n"
            f"🏆 Wins:\n{wins}\n\n"
            f"🪨 Stuck:\n{stuck}\n\n"
            f"🎯 Next week:\n{goals}\n\n"
            f"🤔 Reflect:\n{questions}\n\n"
            f"_{summary}_"
        )
