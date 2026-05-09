"""Code generation skill — write, explain or debug code."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


CODE_PROMPT = """You are an expert software engineer.

Task: {request}

Relevant user context:
{context}

Reply with:
- A brief plan (2-3 lines).
- The code in a fenced block, with file paths if multiple files.
- Notes on assumptions or how to run it.

Keep it focused and clean."""


class CodeGeneratorSkill(BaseSkill):
    name = "code_generator"
    description = "Generate, explain, or debug code."
    triggers = [
        "/code", "write code", "generate code", "code for",
        "debug this", "explain this code", "fix this code",
        "python script", "shell script",
    ]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/code"):
            return 0.95
        if any(t in m for t in ("write code", "generate code", "code for", "debug this", "fix this code")):
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        request = message
        if request.lower().startswith("/code"):
            parts = message.split(maxsplit=1)
            request = parts[1].strip() if len(parts) > 1 else ""
        text = await self.llm.complete(
            prompt=CODE_PROMPT.format(
                request=request,
                context=context.to_prompt_block()[:2000],
            ),
            system="You write production-quality code with concise explanations.",
            model_tier="powerful",
            max_tokens=2000,
        )
        return SkillResponse(text=text)
