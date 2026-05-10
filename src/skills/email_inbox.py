"""List + summarise unread Gmail messages, with inline actions per message
and lightweight triage by category. Caches the listing in
``email_state`` so other email skills (read, reply, search) can resolve
references like "el de Anthropic".
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from src.integrations.gmail_client import GmailClient, is_noreply
from src.skills.base import BaseSkill, SkillResponse
from src.skills.email_state import InboxItem, remember_inbox

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


CATEGORIES = ("action", "personal", "newsletter", "other")

TRIAGE_PROMPT = """Classify each email into ONE of: action | personal | newsletter | other.
Rules:
- "action" = needs a human reply, a decision, or a task. Bills, calendar invites, recruiter pings, customer questions.
- "personal" = friends/family/1:1 with a real human, no action needed urgently.
- "newsletter" = digests, marketing, automated transactional (receipts, security alerts), no-reply senders.
- "other" = nothing else fits.

Output ONLY a JSON object: {{"<id>": "category", ...}} with no commentary.

Emails:
{emails}
"""

CATEGORY_ICON = {
    "action": "🔴",
    "personal": "🟡",
    "newsletter": "📰",
    "other": "•",
}


class EmailInboxSkill(BaseSkill):
    name = "email_inbox"
    description = "Read your Gmail inbox: list unread, triage by importance, summarise what matters, surface anything time-sensitive."
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
    input_schema = {
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "Max number of unread emails to fetch (default 10).",
                "default": 10,
            },
        },
        "required": [],
    }

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
                    "Gmail isn't connected yet. Set `GMAIL_CLIENT_ID`, "
                    "`GMAIL_CLIENT_SECRET` and `GMAIL_REFRESH_TOKEN` in `.env` "
                    "and restart the bot."
                ),
                metadata={"skill": self.name, "disabled": True},
            )
        client = self._gmail()
        try:
            unread = client.list_unread(max_results=10)
        except Exception as exc:
            logger.warning("Gmail list_unread failed: %s", exc)
            return SkillResponse(
                text=f"⚠️ Couldn't read Gmail: {exc}",
                metadata={"skill": self.name, "error": str(exc)},
            )

        if not unread:
            return SkillResponse(
                text="✅ Inbox zero — nothing unread.",
                metadata={"skill": self.name, "count": 0},
            )

        # Triage in one shot via Haiku.
        categories = await self._triage(unread)

        items = [
            InboxItem(
                id=m.get("id"),
                thread_id=m.get("thread_id"),
                sender=m.get("from") or "",
                subject=m.get("subject") or "(no subject)",
                snippet=m.get("snippet") or "",
                date=m.get("date") or "",
                category=categories.get(m.get("id"), "other"),
            )
            for m in unread
        ]
        user_id = getattr(context, "user_id", 0) or 0
        remember_inbox(int(user_id), items)

        # Render: group by category, numbered for natural-language reference.
        lines = [f"**📬 {len(items)} unread** — tap a message or say _\"reply to the one from X\"_:\n"]
        for i, it in enumerate(items, 1):
            icon = CATEGORY_ICON.get(it.category, "•")
            tag = " · _no-reply_" if is_noreply(it.sender) else ""
            sender_short = (it.sender or "").split("<")[0].strip().strip('"') or "(unknown)"
            subj = (it.subject or "")[:80]
            lines.append(f"{icon} **{i}.** {sender_short} — *{subj}*{tag}")
            if it.snippet:
                lines.append(f"     _{it.snippet[:140]}_")

        # Inline buttons: 1-row of quick actions per email (max 5 to fit Telegram)
        buttons: list[list[dict[str, str]]] = []
        for i, it in enumerate(items[:5], 1):
            buttons.append([
                {"text": f"📖 Read #{i}", "callback_data": f"email:read:{it.id}"},
                {"text": f"↩️ Reply #{i}", "callback_data": f"email:reply:{it.id}"},
                {"text": f"🗑 Archive #{i}", "callback_data": f"email:archive:{it.id}"},
            ])

        return SkillResponse(
            text="\n".join(lines),
            inline_buttons=buttons,
            metadata={
                "skill": self.name,
                "count": len(items),
                "ids": [it.id for it in items],
            },
        )

    async def _triage(self, messages: list[dict]) -> dict[str, str]:
        try:
            payload = "\n".join(
                f"- id={m.get('id')} | from={m.get('from')} | subject={m.get('subject')} "
                f"| snippet={(m.get('snippet') or '')[:160]}"
                for m in messages
            )
            raw = await self.llm.complete(
                prompt=TRIAGE_PROMPT.format(emails=payload),
                system="You output strict JSON. No prose.",
                model_tier="tiny",
                max_tokens=400,
                temperature=0.0,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)
            return {k: v for k, v in data.items() if v in CATEGORIES}
        except Exception as exc:
            logger.debug("triage failed, defaulting all to 'other': %s", exc)
            return {}
