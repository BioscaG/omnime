"""Email drafting skill — produces drafts for user review before sending."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


EMAIL_PROMPT = """Draft an email on behalf of the user.

User profile (for tone matching):
{profile}

Communication style: {style}

Relevant context:
{context}

User request:
{request}

Output strictly:
Subject: <subject line>

<email body, signed naturally>

Rules:
- Match the user's voice and style.
- Detect language from the request and reply in that language.
- Be appropriately formal/casual for the relationship described.
- No closing pleasantries on top of a signature unless natural.
"""


class EmailComposerSkill(BaseSkill):
    name = "email_composer"
    description = "Draft an email (subject + body) for the user to review before sending."
    triggers = [
        "/email", "draft an email", "draft email", "write an email",
        "compose email", "respond to", "reply to", "answer this email",
        "escribe un correo", "redacta email", "respóndele a", "contéstale a",
    ]
    examples = [
        "redacta un email a Marc cancelando la reunión del jueves",
        "draft a reply to my landlord about the deposit",
    ]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/email"):
            return 0.95
        if any(t in m for t in ("draft an email", "draft email", "write an email", "compose email")):
            return 0.9
        if any(t in m for t in ("reply to", "respond to", "answer this email")):
            return 0.8
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        profile = context.profile or {}
        request = self._strip_command(message)

        body = await self.llm.complete(
            prompt=EMAIL_PROMPT.format(
                profile=profile.get("living_profile") or profile.get("bio") or "(unknown)",
                style=profile.get("communication_style") or "neutral, friendly",
                context=context.to_prompt_block()[:2000],
                request=request,
            ),
            system="You write authentic, on-tone emails for the user.",
            model_tier="powerful",
            max_tokens=900,
        )

        return SkillResponse(
            text="Draft:\n\n" + body,
            inline_buttons=[[
                {"text": "✅ Send", "callback_data": "email:send"},
                {"text": "✏️ Edit", "callback_data": "email:edit"},
                {"text": "❌ Cancel", "callback_data": "email:cancel"},
            ]],
            metadata={"draft": body, "skill": self.name},
        )

    @staticmethod
    def _strip_command(message: str) -> str:
        if message.strip().lower().startswith("/email"):
            parts = message.split(maxsplit=1)
            return parts[1].strip() if len(parts) > 1 else ""
        return message.strip()
