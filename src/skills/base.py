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
    examples: list[str] = []

    # JSON-schema describing this skill's parameters when it's invoked through
    # the agentic tool-use loop. Subclasses override with rich, typed fields
    # (e.g. ``email_composer`` exposes ``to`` / ``reply_to_id`` / ``instruction``).
    # The default lets the loop pass through the raw user request as a free-form
    # ``instruction`` string — works for skills whose argument is "do this".
    input_schema: dict = {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": "The user's natural-language instruction for this skill, verbatim.",
            },
        },
        "required": ["instruction"],
    }

    @property
    def enabled(self) -> bool:
        """Skills can override this to hide themselves when unavailable
        (e.g. Gmail skill when OAuth isn't configured)."""
        return True

    @abstractmethod
    async def execute(self, message: str, context: "Context") -> SkillResponse:
        ...

    async def execute_with_args(self, args: dict, context: "Context") -> SkillResponse:
        """Entry point used by the agentic tool-use loop. Default: synthesise
        a natural-language message from ``args`` (joining the values) and
        call ``execute``. Skills with rich schemas should override this."""
        if not args:
            synthetic = ""
        elif "instruction" in args and isinstance(args["instruction"], str):
            synthetic = args["instruction"]
        else:
            synthetic = " ".join(str(v) for v in args.values() if v)
        return await self.execute(synthetic, context)

    def can_handle(self, message: str, intent: str | None = None) -> float:
        """Score 0..1 for how likely this skill should handle the message."""
        msg = message.lower()
        score = 0.0
        for t in self.triggers:
            if t.lower() in msg:
                score = max(score, 0.7)
        return score

    def catalog_line(self) -> str:
        """One-line entry for the capability catalog injected into the system
        prompt and the routing tool description."""
        line = f"- `{self.name}` — {self.description}"
        if self.examples:
            sample = " / ".join(f'"{e}"' for e in self.examples[:2])
            line += f"  (e.g. {sample})"
        return line
