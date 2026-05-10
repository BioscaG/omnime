"""Daily briefing — combines stored data + (optional) Gmail/Calendar."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


BRIEFING_PROMPT = """Write a concise daily briefing for the user.

Today: {today}

Stored context:
{stored}

Recent emails (top {n_emails}):
{emails}

Calendar today:
{calendar}

Pending tasks/ideas:
{tasks}

Format:
☀️ Good morning {name}!

📩 Inbox: ...
📅 Today: ...
📝 Pending: ...
💡 Heads up: ...

Be brief, scannable, no fluff.
"""


class DailyBriefingSkill(BaseSkill):
    name = "daily_briefing"
    description = "Personalised morning briefing: today's events, pending tasks, top priorities."
    triggers = [
        "/briefing", "daily briefing", "morning summary", "brief me",
        "dame el resumen del día", "qué tengo hoy", "what's on today",
    ]
    examples = ["dame el briefing de hoy", "what does my day look like?"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/briefing"):
            return 0.95
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        profile = context.profile or {}
        emails_block = self._fetch_emails()
        calendar_block = self._fetch_calendar()

        active_projects = ", ".join(
            p["name"] for p in (profile.get("projects") or []) if p.get("status") == "active"
        )
        text = await self.llm.complete(
            prompt=BRIEFING_PROMPT.format(
                today=date.today().isoformat(),
                stored=f"Active projects: {active_projects}",
                n_emails=5,
                emails=emails_block,
                calendar=calendar_block,
                tasks="(no task tracker connected)",
                name=profile.get("name") or "",
            ),
            system="You write punchy morning briefings.",
            model_tier="fast",
            max_tokens=600,
        )
        return SkillResponse(text=text, metadata={"date": date.today().isoformat()})

    def _fetch_emails(self) -> str:
        try:
            from src.integrations.gmail_client import GmailClient

            client = GmailClient()
            if not client.enabled:
                return "(Gmail not configured)"
            messages = client.list_unread(max_results=5)
            if not messages:
                return "(no unread)"
            return "\n".join(
                f"- {m.get('subject', '(no subject)')} — {m.get('from', '')}" for m in messages
            )
        except Exception as exc:
            logger.warning("Email fetch failed: %s", exc)
            return "(unavailable)"

    def _fetch_calendar(self) -> str:
        try:
            from src.integrations.calendar_client import CalendarClient

            client = CalendarClient()
            if not client.enabled:
                return "(Calendar not configured)"
            today_start = datetime.combine(date.today(), datetime.min.time())
            tomorrow = today_start + timedelta(days=1)
            events = client.list_events(today_start, tomorrow)
            if not events:
                return "(no events)"
            return "\n".join(
                f"- {e.get('start')}–{e.get('end')}: {e.get('summary')}" for e in events
            )
        except Exception as exc:
            logger.warning("Calendar fetch failed: %s", exc)
            return "(unavailable)"
