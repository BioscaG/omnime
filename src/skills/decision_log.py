"""Decision log: list past decisions and surface relevant ones for new dilemmas."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


SIMILAR_PROMPT = """The user is facing a decision and wants to know if they've made similar ones before.

Current situation:
{situation}

Past decisions on file:
{past}

Identify the 2-3 most similar past decisions, summarise the lesson learned (good or bad),
and suggest how it should inform the current call.

Markdown, concise."""


class DecisionLogSkill(BaseSkill):
    name = "decision_log"
    description = "List, recall and reason about your past decisions."
    triggers = ["/decisions", "/decide", "decision log", "what did i decide"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/decisions") or m.startswith("/decide"):
            return 0.95
        if "decision log" in m or "what did i decide" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        argument = self._strip_command(message)
        if argument:
            return await self._recall(context, argument)
        return self._list(context)

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        for tok in ("/decisions", "/decide"):
            if m.lower().startswith(tok):
                return m[len(tok):].strip()
        return m

    def _list(self, context: "Context") -> SkillResponse:
        with session_scope() as s:
            decisions = StructuredStore(s).list_decisions(context.user_id)
        if not decisions:
            return SkillResponse(
                text=(
                    "No decisions recorded yet.\n"
                    "Tell me about decisions you make and I'll log them. "
                    "Or `/decide <situation>` to recall similar past calls."
                )
            )
        lines = ["🧭 Decision log:"]
        for d in decisions[:25]:
            when = d.decided_at.isoformat() if d.decided_at else "—"
            outcome = f" → {d.outcome[:80]}" if d.outcome else ""
            lines.append(f"• [{when}] **{d.title}** ({d.status}){outcome}")
        return SkillResponse(text="\n".join(lines))

    async def _recall(self, context: "Context", situation: str) -> SkillResponse:
        with session_scope() as s:
            decisions = StructuredStore(s).list_decisions(context.user_id)
        if not decisions:
            return SkillResponse(text="No past decisions to compare against.")
        past = "\n".join(
            f"- [{d.decided_at or '—'}] {d.title}: rationale={d.rationale or 'n/a'}; "
            f"outcome={d.outcome or 'pending'}"
            for d in decisions[:25]
        )
        text = await self.llm.complete(
            prompt=SIMILAR_PROMPT.format(situation=situation, past=past),
            system="You are a careful adviser who values the user's own past lessons.",
            model_tier="powerful",
            max_tokens=1200,
        )
        return SkillResponse(text=text)
