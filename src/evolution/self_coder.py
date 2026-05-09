"""Generate new skills on demand and gate deployment behind user approval."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from src.evolution.deployer import Deployer
from src.evolution.sandbox import Sandbox

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.brain.orchestrator import Response
    from src.memory.manager import MemoryManager
    from src.skills.registry import SkillRegistry


logger = logging.getLogger(__name__)


CODER_PROMPT = """You write Python skill modules for OMNIME, a personal AI assistant.

Existing base interface (already importable):
    from src.skills.base import BaseSkill, SkillResponse

Constraints:
- Module must contain ONE class subclassing BaseSkill.
- The constructor takes (llm, memory).
- Implement async def execute(self, message, context) -> SkillResponse.
- Implement def can_handle(self, message, intent=None) -> float.
- Set: name, description, triggers (class attributes).
- No `import os.system` or arbitrary shell calls. No network unless required for the task.
- Keep dependencies to ones already in requirements.txt.
- Return only Python code, no preamble, no fences.

Capability requested:
\"\"\"
{request}
\"\"\"

User context:
{context}
"""


@dataclass
class PendingSkill:
    name: str
    code: str
    summary: str
    smoke: dict[str, Any] = field(default_factory=dict)


class EvolutionEngine:
    def __init__(
        self,
        llm: "LLMClient",
        memory: "MemoryManager",
        registry: "SkillRegistry",
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.registry = registry
        self.sandbox = Sandbox()
        self.deployer = Deployer(registry)
        self._pending: dict[int, PendingSkill] = {}

    async def handle(
        self,
        user_id: int,
        message: str,
        context: "Context",
    ) -> "Response":
        from src.brain.orchestrator import Intent, Response

        request = re.sub(r"^EVOLVE:\s*", "", message, flags=re.IGNORECASE).strip()
        if not request:
            return Response(text="Tell me what new capability you'd like me to add.", intent=Intent.EVOLVE)

        prompt = CODER_PROMPT.format(
            request=request, context=context.to_prompt_block()[:2000]
        )
        code = await self.llm.complete(
            prompt=prompt,
            system="You are an expert Python engineer writing clean, working modules.",
            model_tier="powerful",
            max_tokens=2500,
        )
        code = self._strip_fences(code)

        smoke = self.sandbox.smoke_test(code)
        if not smoke.get("ok"):
            return Response(
                text=(
                    "I drafted code but the smoke test failed:\n"
                    f"Stage: {smoke.get('stage')}\n"
                    f"Error: {(smoke.get('error') or '')[:1500]}"
                ),
                intent=Intent.EVOLVE,
            )

        skill_name = (smoke.get("classes") or ["NewSkill"])[0]
        pending = PendingSkill(name=skill_name, code=code, summary=request, smoke=smoke)
        self._pending[user_id] = pending

        preview = code[:1500] + ("\n# ...truncated..." if len(code) > 1500 else "")
        text = (
            f"I've drafted a new skill: *{skill_name}*\n"
            f"Summary: {request}\n\n"
            f"```python\n{preview}\n```\n"
            "Approve to install + reload."
        )
        return Response(
            text=text,
            intent=Intent.EVOLVE,
            inline_buttons=[[
                {"text": "✅ Approve & Install", "callback_data": "evolve:approve"},
                {"text": "❌ Reject", "callback_data": "evolve:reject"},
            ]],
            metadata={"skill_name": skill_name},
        )

    async def approve_pending(self, user_id: int, push_to_github: bool = False) -> str:
        pending = self._pending.pop(user_id, None)
        if pending is None:
            return "No pending skill to approve."
        result = self.deployer.deploy(
            skill_name=pending.name, code=pending.code, push_to_github=push_to_github,
        )
        return f"Installed at {result['path']}, reloaded into registry."

    def reject_pending(self, user_id: int) -> Optional[PendingSkill]:
        return self._pending.pop(user_id, None)

    @staticmethod
    def _strip_fences(s: str) -> str:
        s = s.strip()
        m = re.match(r"^```(?:python)?\s*(.*?)\s*```$", s, re.S)
        return m.group(1) if m else s
