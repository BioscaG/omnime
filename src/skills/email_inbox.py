"""Read-side counterpart to ``email_composer``: lists unread Gmail messages
and (with a follow-up) summarises a specific thread.

Disabled when Gmail OAuth credentials aren't set — in that case the skill
hides itself from the capability catalog so the LLM doesn't promise something
it can't deliver.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.integrations.gmail_client import GmailClient
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


SUMMARY_PROMPT = """You're triaging the user's inbox. For each message below
output exactly one bullet:
- **From** — *Subject* — one-sentence gist of why it matters.

Skip newsletters and marketing unless they're flagged urgent. If everything
is noise, just say "Nothing important — only newsletters/promos."

Messages:
{messages}
"""


class EmailInboxSkill(BaseSkill):
    name = "email_inbox"
    description = "Read your Gmail inbox: list unread, summarise what matters, surface anything time-sensitive."
    triggers = [
        "/inbox", "/mail", "/email_check", "/correos",
        "check my email", "check my inbox", "any new email",
        "mira mi email", "mira mis correos", "tengo correos sin leer",
        "qué hay en mi bandeja", "what's in my inbox",
    ]
    examples = [
        "mira mi email",
        "check my inbox and summarise what's important",
    ]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory
        self._client: GmailClient | None = None

    @property
    def enabled(self) -> bool:
        try:
            return GmailClient().enabled
        except Exception:
            return False

    def _gmail(self) -> GmailClient:
        if self._client is None:
            self._client = GmailClient()
        return self._client

    def can_handle(self, message: str, intent: str | None = None) -> float:
        if not self.enabled:
            return 0.0
        m = message.lower().strip()
        if m.startswith(("/inbox", "/mail", "/email_check", "/correos")):
            return 0.95
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        if not self.enabled:
            return SkillResponse(
                text=(
                    "Gmail no está conectado todavía. Configura "
                    "`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` y "
                    "`GMAIL_REFRESH_TOKEN` en `.env` y reinicia el bot."
                ),
                metadata={"skill": self.name, "disabled": True},
            )
        client = self._gmail()
        try:
            unread = client.list_unread(max_results=10)
        except Exception as exc:
            logger.warning("Gmail list_unread failed: %s", exc)
            return SkillResponse(
                text=f"⚠️ No pude leer Gmail: {exc}",
                metadata={"skill": self.name, "error": str(exc)},
            )

        if not unread:
            return SkillResponse(
                text="✅ Bandeja al día — ningún correo sin leer.",
                metadata={"skill": self.name, "count": 0},
            )

        rendered = "\n".join(
            f"- From: {m.get('from')} | Subject: {m.get('subject')} | "
            f"Snippet: {(m.get('snippet') or '')[:200]}"
            for m in unread
        )
        summary = await self.llm.complete(
            prompt=SUMMARY_PROMPT.format(messages=rendered),
            system="You are a fast, ruthless inbox triager.",
            model_tier="fast",
            max_tokens=600,
        )
        header = f"**📬 {len(unread)} correos sin leer**\n\n"
        return SkillResponse(
            text=header + summary.strip(),
            metadata={
                "skill": self.name,
                "count": len(unread),
                "ids": [m.get("id") for m in unread],
            },
        )
