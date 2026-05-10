"""Open a single Gmail message: pull the full body and summarise it.

Resolves natural-language references against the cached inbox: "lee el de
Anthropic", "abre el #3", "open the Renfe email". Falls back to gmail
search when the inbox cache is stale or the reference doesn't match.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from src.integrations.gmail_client import GmailClient, is_noreply
from src.skills.base import BaseSkill, SkillResponse
from src.skills.email_state import find_message, get_inbox, remember_opened

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


READ_SUMMARY_PROMPT = """Summarise the following email for a busy person:
1. **TL;DR** in one sentence (the actual point of the email).
2. **Key facts** — dates, numbers, names, links worth noting (3-5 bullets max).
3. **Suggested action** — should the user reply? Save it? Ignore? One line.

Match the language of the email body in your reply.

----- EMAIL -----
From: {sender}
Subject: {subject}
Date: {date}

{body}
"""


class EmailReadSkill(BaseSkill):
    name = "email_read"
    description = "Open + summarise the full body of a specific email (resolves references like 'el de Anthropic' or '#3' against your last inbox listing)."
    triggers = [
        "/read", "/open_email", "/abrir",
        "open email", "show me the email", "read the email",
        "lee el email", "abre el correo", "muéstrame el email",
        "lee el de", "abre el de", "show the one from",
    ]
    examples = [
        "lee el de Anthropic",
        "open the email from my landlord",
        "abre el #3",
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
        if m.startswith(("/read", "/open_email", "/abrir")):
            return 0.95
        if any(p in m for p in ("lee el", "abre el", "open the", "read the")) and any(
            p in m for p in ("email", "correo", "mensaje", "mail")
        ):
            return 0.7
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

        user_id = getattr(context, "user_id", 0) or 0
        hint = self._extract_hint(message)
        target = find_message(int(user_id), hint)

        if target is None and not get_inbox(int(user_id)):
            try:
                fallback = self._gmail().search(self._to_gmail_query(hint), max_results=5)
            except Exception as exc:
                return SkillResponse(
                    text=f"⚠️ Couldn't search Gmail: {exc}",
                    metadata={"skill": self.name, "error": str(exc)},
                )
            if not fallback:
                return SkillResponse(
                    text=f"No email matched **{hint or '(empty hint)'}**. Try `/inbox` first or be more specific.",
                    metadata={"skill": self.name, "miss": True},
                )
            from src.skills.email_state import InboxItem
            target = InboxItem(
                id=fallback[0]["id"],
                thread_id=fallback[0].get("thread_id") or "",
                sender=fallback[0].get("from") or "",
                subject=fallback[0].get("subject") or "",
                snippet=fallback[0].get("snippet") or "",
                date=fallback[0].get("date") or "",
            )

        if target is None:
            return SkillResponse(
                text=(
                    f"No email in your last inbox listing matched **{hint or '(empty)'}**. "
                    "Refresh with `/inbox` and try again."
                ),
                metadata={"skill": self.name, "miss": True},
            )

        try:
            parsed = self._gmail().get_message_parsed(target.id)
        except Exception as exc:
            logger.exception("Gmail get_message_parsed failed")
            return SkillResponse(
                text=f"⚠️ Couldn't open that email: {exc}",
                metadata={"skill": self.name, "error": str(exc)},
            )

        body = (parsed.get("body") or "")[:8000]
        remember_opened(int(user_id), target.id, body)

        # Mark as read on the server side too, since the user is reading it now.
        try:
            self._gmail().mark_read(target.id)
        except Exception as exc:
            logger.debug("mark_read failed: %s", exc)

        summary = await self.llm.complete(
            prompt=READ_SUMMARY_PROMPT.format(
                sender=parsed.get("from") or "(unknown)",
                subject=parsed.get("subject") or "(no subject)",
                date=parsed.get("date") or "",
                body=body or "(empty body)",
            ),
            system="You are a fast, accurate email summariser.",
            model_tier="fast",
            max_tokens=600,
        )

        warn = ""
        if is_noreply(parsed.get("from") or ""):
            warn = "\n\n⚠️ This is a no-reply address — replies will likely bounce."

        text = (
            f"**📧 {parsed.get('subject') or '(no subject)'}**\n"
            f"_From {parsed.get('from')} · {parsed.get('date') or ''}_\n\n"
            f"{summary.strip()}{warn}"
        )

        buttons: list[list[dict[str, str]]] = []
        if not is_noreply(parsed.get("from") or ""):
            buttons.append([
                {"text": "↩️ Reply", "callback_data": f"email:reply:{target.id}"},
                {"text": "📤 Forward", "callback_data": f"email:forward:{target.id}"},
                {"text": "🗑 Archive", "callback_data": f"email:archive:{target.id}"},
            ])
        else:
            buttons.append([
                {"text": "🗑 Archive", "callback_data": f"email:archive:{target.id}"},
            ])

        return SkillResponse(
            text=text,
            inline_buttons=buttons,
            metadata={
                "skill": self.name,
                "message_id": target.id,
                "thread_id": target.thread_id,
            },
        )

    @staticmethod
    def _extract_hint(message: str) -> str:
        m = message.strip()
        for prefix in ("/read", "/open_email", "/abrir"):
            if m.lower().startswith(prefix):
                m = m[len(prefix):].strip()
                break
        # Strip filler phrasings.
        m = re.sub(
            r"^(lee|abre|open|read|show me|muéstrame)\s+(el|the)?\s*(email|correo|mensaje|mail)?\s*(de|from|del|of)?\s*",
            "",
            m,
            flags=re.I,
        )
        return m.strip().strip('"').strip("'")

    @staticmethod
    def _to_gmail_query(hint: str) -> str:
        if not hint:
            return "in:inbox"
        # If looks like an email address or domain, search by from:
        if "@" in hint or "." in hint:
            return f"from:{hint}"
        return f"({hint})"
