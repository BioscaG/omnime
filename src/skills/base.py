"""Skill interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.brain.context_builder import Context


@dataclass
class SkillResponse:
    text: str
    inline_buttons: list[list[dict[str, str]]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseSkill(ABC):
    name: str = "base"
    description: str = ""
    triggers: list[str] = []

    @abstractmethod
    async def execute(self, message: str, context: "Context") -> SkillResponse:
        ...

    def can_handle(self, message: str, intent: str | None = None) -> float:
        """Score 0..1 for how likely this skill should handle the message."""
        msg = message.lower()
        score = 0.0
        for t in self.triggers:
            if t.lower() in msg:
                score = max(score, 0.7)
        return score
