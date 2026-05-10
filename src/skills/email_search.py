"""Search Gmail with natural-language → Gmail-query translation.

User says "busca correos de Renfe del mes pasado" → we translate it to
``from:renfe.com after:YYYY/MM/DD before:YYYY/MM/DD`` and execute.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from src.integrations.gmail_client import GmailClient
from src.skills.base import BaseSkill, SkillResponse
from src.skills.email_state import InboxItem, remember_inbox

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


QUERY_TRANSLATE = """Translate the user's request into a Gmail search query.

Gmail operators you may use:
- from:<email-or-domain>
- to:<email>
- subject:<word>
- has:attachment
- is:unread / is:read
- in:inbox / in:sent / in:anywhere
- after:YYYY/MM/DD / before:YYYY/MM/DD
- newer_than:Nd (N days)
- bare keywords are matched in body

Today is {today}.

User request: {request}

Reply ONLY with a single line: the Gmail query. No explanation. If the
request is ambiguous, prefer broader queries.
"""


class EmailSearchSkill(BaseSkill):
    name = "email_search"
    description = "Search your Gmail with natural language ('emails from Renfe last month', 'invoices from 2025')."
    triggers = [
        "/search_mail", "/find_email", "/buscar_email",
        "search my email", "find email from",
        "busca correos", "busca el email", "buscar mail",
        "emails about", "correos sobre",
    ]
    examples = [
        "busca correos de Renfe del mes pasado",
        "find emails from glovo about job offers",
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
        if m.startswith(("/search_mail", "/find_email", "/buscar_email")):
            return 0.95
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        if not self.enabled:
            return SkillResponse(
                text="Gmail isn't connected yet. Set `GMAIL_*` env vars first.",
                metadata={"skill": self.name, "disabled": True},
            )
        request = self._strip_command(message)
        if not request:
            return SkillResponse(text="What should I search for?")

        query = await self._translate(request)
        try:
            hits = self._gmail().search(query, max_results=10)
        except Exception as exc:
            logger.warning("Gmail search failed: %s", exc)
            return SkillResponse(
                text=f"⚠️ Search failed: {exc}",
                metadata={"skill": self.name, "error": str(exc)},
            )
        if not hits:
            return SkillResponse(
                text=f"No matches for `{query}`.",
                metadata={"skill": self.name, "query": query, "count": 0},
            )

        items = [
            InboxItem(
                id=h["id"],
                thread_id=h.get("thread_id") or "",
                sender=h.get("from") or "",
                subject=h.get("subject") or "",
                snippet=h.get("snippet") or "",
                date=h.get("date") or "",
            )
            for h in hits
        ]
        user_id = getattr(context, "user_id", 0) or 0
        remember_inbox(int(user_id), items)

        lines = [f"**🔍 {len(items)} matches** (query: `{query}`)\n"]
        for i, it in enumerate(items, 1):
            sender_short = (it.sender or "").split("<")[0].strip().strip('"')
            lines.append(
                f"**{i}.** {sender_short} — *{(it.subject or '')[:80]}* "
                f"_{(it.date or '')[:25]}_"
            )
            if it.snippet:
                lines.append(f"     _{(it.snippet or '')[:140]}_")

        buttons: list[list[dict[str, str]]] = []
        for i, it in enumerate(items[:5], 1):
            buttons.append([
                {"text": f"📖 Read #{i}", "callback_data": f"email:read:{it.id}"},
                {"text": f"↩️ Reply #{i}", "callback_data": f"email:reply:{it.id}"},
            ])

        return SkillResponse(
            text="\n".join(lines),
            inline_buttons=buttons,
            metadata={
                "skill": self.name,
                "query": query,
                "count": len(items),
                "ids": [it.id for it in items],
            },
        )

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        for prefix in ("/search_mail", "/find_email", "/buscar_email"):
            if m.lower().startswith(prefix):
                m = m[len(prefix):].strip()
                break
        return m.strip()

    async def _translate(self, request: str) -> str:
        # Quick fallback: if the user wrote a Gmail-shaped query, pass through.
        if re.search(r"\b(from|to|subject|after|before|newer_than|has):", request):
            return request
        try:
            today = datetime.now().strftime("%Y/%m/%d")
            raw = await self.llm.complete(
                prompt=QUERY_TRANSLATE.format(today=today, request=request),
                system="You output a single Gmail search query.",
                model_tier="tiny",
                max_tokens=80,
                temperature=0.0,
            )
            line = (raw or "").strip().splitlines()[0].strip("` ")
            return line or request
        except Exception as exc:
            logger.debug("query translate failed: %s", exc)
            return request
