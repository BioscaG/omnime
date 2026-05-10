"""Agentic skill: decompose a complex goal and execute through the planner."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


class AgenticSkill(BaseSkill):
    name = "agentic"
    description = "Decompose a complex goal into a multi-step plan and run it."
    triggers = ["/plan", "/agent", "agentic", "plan and execute", "multi-step"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/plan") or m.startswith("/agent"):
            return 0.95
        if "plan and execute" in m or "multi-step" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        goal = self._strip_command(message)
        if not goal:
            return SkillResponse(text="Give me a goal after /plan.")

        from src.brain.context_builder import ContextBuilder
        from src.brain.planner import Planner
        from src.skills.registry import get_registry

        registry = get_registry()
        planner = Planner(
            llm=self.llm, registry=registry, builder=ContextBuilder(self.memory)
        )
        result = await planner.run(context.user_id, goal)
        return SkillResponse(
            text=result.final_text,
            metadata={
                "steps": [
                    {"skill": s.skill, "why": s.why, "input": s.input}
                    for s in result.steps
                ]
            },
        )

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        for tok in ("/plan", "/agent"):
            if m.lower().startswith(tok):
                return m[len(tok):].strip()
        return m
