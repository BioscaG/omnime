"""Compose emails — new threads or replies — and stash the draft for the
delayed-send flow.

Reply-awareness: when the user says "responde al de Anthropic" (or similar),
we resolve the reference against the cached inbox state and pull the
original body via Gmail so the draft has real context. We also extract a
clean ``to`` address from the original ``Reply-To`` / ``From`` header so
the user doesn't need to remember it.

Drafts are stashed in ``email_state`` so the inline-button callback can
schedule the send (default 10 minutes, with cancel + reschedule options)
without round-tripping the body through Telegram callback_data (which is
limited to 64 bytes).
"""
from __future__ import annotations

import logging
import re
from email.utils import parseaddr
from typing import TYPE_CHECKING

from src.integrations.gmail_client import GmailClient, is_noreply
from src.skills.base import BaseSkill, SkillResponse
from src.skills.email_state import (
    find_message,
    get_inbox,
    get_last_opened,
    render_inbox_index,
    stash_draft,
)

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


REPLY_TRIGGERS = re.compile(
    r"\b(reply|respond|reply to|answer|respond to|"
    r"responde|contesta|contéstale|respóndele|contestá)\b",
    re.I,
)


COMPOSE_PROMPT = """Draft an email on behalf of the user.

User profile (for tone matching):
{profile}

Communication style: {style}

Relevant context:
{context}

User request:
{request}

Output strictly:
TO: <recipient email — only if the user named one explicitly; otherwise leave blank>
SUBJECT: <subject line>

<email body, signed naturally>

Rules:
- Match the user's voice and style.
- Detect language from the request (or from the original email when replying) and reply in that language.
- Be appropriately formal/casual for the relationship described.
- Don't pad with empty pleasantries.
"""


REPLY_PROMPT = """Draft a REPLY to the email below, on behalf of the user.

User profile (for tone matching):
{profile}

Communication style: {style}

Original email being replied to:
-----
From: {orig_from}
Subject: {orig_subject}
Date: {orig_date}

{orig_body}
-----

User's reply instruction: {request}

Output strictly:
TO: {orig_from_addr}
SUBJECT: {reply_subject}

<reply body, signed naturally>

Rules:
- Match the original email's language.
- Quote sparingly — only what's needed to make the reply self-contained.
- Be concise. The recipient already knows what they wrote.
- If the user's instruction is "say I'll get back later" or similar short
  intent, write a complete polite reply that conveys exactly that.
"""


class EmailComposerSkill(BaseSkill):
    name = "email_composer"
    description = "Draft an email — a new thread or a reply to a specific inbox message — and queue it for review/scheduled send."
    triggers = [
        "/email", "draft an email", "draft email", "write an email",
        "compose email", "respond to", "reply to", "answer this email",
        "escribe un correo", "redacta email", "respóndele a", "contéstale a",
    ]
    examples = [
        "redacta un email a Marc cancelando la reunión del jueves",
        "responde al de Anthropic diciendo que tengo la factura guardada",
    ]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory
        self._client: GmailClient | None = None

    @property
    def enabled(self) -> bool:
        return True  # Compose works even without Gmail (just won't send)

    def _gmail_optional(self) -> GmailClient | None:
        try:
            client = GmailClient()
            if not client.enabled:
                return None
            if self._client is None:
                self._client = client
            return self._client
        except Exception:
            return None

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/email"):
            return 0.95
        if any(t in m for t in ("draft an email", "draft email", "write an email", "compose email")):
            return 0.9
        if REPLY_TRIGGERS.search(m):
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        request = self._strip_command(message)
        user_id = int(getattr(context, "user_id", 0) or 0)
        original = await self._maybe_fetch_original(user_id, request)

        if original:
            return await self._compose_reply(user_id, request, original, context)
        return await self._compose_new(user_id, request, context)

    # --- New email -----------------------------------------------------
    async def _compose_new(self, user_id: int, request: str, context: "Context") -> SkillResponse:
        profile = context.profile or {}
        body_raw = await self.llm.complete(
            prompt=COMPOSE_PROMPT.format(
                profile=profile.get("living_profile") or profile.get("bio") or "(unknown)",
                style=profile.get("communication_style") or "neutral, friendly",
                context=context.to_prompt_block()[:2000],
                request=request or "(no specific instruction — draft a reasonable opener)",
            ),
            system="You write authentic, on-tone emails for the user.",
            model_tier="powerful",
            max_tokens=900,
        )
        to, subject, body = self._parse_draft(body_raw)
        return self._respond_with_draft(
            user_id=user_id,
            to=to or "",
            subject=subject or "(no subject)",
            body=body,
            in_reply_to=None,
            references=None,
            thread_id=None,
        )

    # --- Reply ---------------------------------------------------------
    async def _compose_reply(
        self,
        user_id: int,
        request: str,
        original: dict,
        context: "Context",
    ) -> SkillResponse:
        profile = context.profile or {}
        from_header = original.get("from") or ""
        orig_from_name, orig_from_addr = parseaddr(from_header)

        if is_noreply(from_header):
            return SkillResponse(
                text=(
                    f"⚠️ The email from **{orig_from_name or orig_from_addr}** is "
                    f"sent from a no-reply address (`{orig_from_addr}`). A reply "
                    "would bounce. Want me to draft a fresh email to a real "
                    "contact instead? Tell me who."
                ),
                metadata={"skill": self.name, "noreply": True},
            )

        orig_subject = original.get("subject") or ""
        reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"

        body_raw = await self.llm.complete(
            prompt=REPLY_PROMPT.format(
                profile=profile.get("living_profile") or profile.get("bio") or "(unknown)",
                style=profile.get("communication_style") or "neutral, friendly",
                orig_from=from_header,
                orig_from_addr=orig_from_addr,
                orig_subject=orig_subject,
                orig_date=original.get("date") or "",
                orig_body=(original.get("body") or "")[:4000],
                reply_subject=reply_subject,
                request=request or "(write a sensible reply based on the original)",
            ),
            system="You write authentic, on-tone replies for the user.",
            model_tier="powerful",
            max_tokens=900,
        )
        to, subject, body = self._parse_draft(body_raw)
        to = to or orig_from_addr
        subject = subject or reply_subject

        return self._respond_with_draft(
            user_id=user_id,
            to=to,
            subject=subject,
            body=body,
            in_reply_to=original.get("message_id_header"),
            references=original.get("references"),
            thread_id=original.get("thread_id"),
            replying_to_subject=orig_subject,
        )

    # --- Helpers -------------------------------------------------------
    async def _maybe_fetch_original(self, user_id: int, request: str) -> dict | None:
        """If the user is asking for a reply, resolve which message and
        fetch its parsed body."""
        if not REPLY_TRIGGERS.search(request or ""):
            return None
        gmail = self._gmail_optional()
        if gmail is None:
            return None

        # 1. Did they just open one? Use that.
        last_id, _ = get_last_opened(user_id)

        # 2. Try to resolve from inbox cache by reference.
        target = None
        hint = self._reference_hint(request)
        if hint or get_inbox(user_id):
            target = find_message(user_id, hint)

        message_id = (target.id if target else None) or last_id
        if not message_id:
            return None

        try:
            return gmail.get_message_parsed(message_id)
        except Exception as exc:
            logger.warning("Couldn't fetch original message %s: %s", message_id, exc)
            return None

    @staticmethod
    def _reference_hint(request: str) -> str:
        # Strip the leading "responde al / reply to / answer the" parts.
        m = re.sub(
            r"^(responde|contesta|reply to|respond to|answer|respond)\s+(al|el|al de|the|to the)?\s*",
            "",
            request.strip(),
            flags=re.I,
        )
        # Often phrased as "reply to the X email saying Y" — keep just the X.
        m = re.split(r"\b(saying|diciendo|telling|que|that)\b", m, maxsplit=1, flags=re.I)[0]
        return m.strip().strip('"').strip("'")

    @staticmethod
    def _parse_draft(raw: str) -> tuple[str, str, str]:
        """Pull TO / SUBJECT / body out of the LLM output."""
        text = raw.strip()
        to = ""
        subject = ""
        body_lines: list[str] = []
        body_started = False
        for line in text.splitlines():
            if not body_started:
                m_to = re.match(r"^TO:\s*(.*)$", line, re.I)
                m_subj = re.match(r"^SUBJECT:\s*(.*)$", line, re.I)
                if m_to:
                    to = m_to.group(1).strip()
                    continue
                if m_subj:
                    subject = m_subj.group(1).strip()
                    continue
                if line.strip() == "":
                    if subject:
                        body_started = True
                    continue
                body_started = True
            body_lines.append(line)
        return to, subject, "\n".join(body_lines).strip()

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        if m.lower().startswith("/email"):
            parts = m.split(maxsplit=1)
            return parts[1].strip() if len(parts) > 1 else ""
        return m

    def _respond_with_draft(
        self,
        *,
        user_id: int,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None,
        references: str | None,
        thread_id: str | None,
        replying_to_subject: str | None = None,
    ) -> SkillResponse:
        gmail_enabled = self._gmail_optional() is not None
        draft = {
            "to": to,
            "subject": subject,
            "body": body,
            "in_reply_to": in_reply_to,
            "references": references,
            "thread_id": thread_id,
        }
        stash_draft(user_id, draft)

        header_lines = [
            f"**📝 Draft ready**",
            f"**To:** {to or '_(unspecified — tell me before sending)_'}",
            f"**Subject:** {subject}",
        ]
        if replying_to_subject:
            header_lines.append(f"_Reply to:_ {replying_to_subject}")
        text = "\n".join(header_lines) + "\n\n---\n\n" + body

        if gmail_enabled and to:
            text += "\n\n_Send is **delayed 10 minutes** by default — you can cancel anytime in that window._"
            buttons: list[list[dict[str, str]]] = [
                [
                    {"text": "📨 Send now", "callback_data": "email:send:now"},
                    {"text": "⏰ Send in 10 min", "callback_data": "email:send:10m"},
                ],
                [
                    {"text": "🕐 Send in 1h", "callback_data": "email:send:1h"},
                    {"text": "✏️ Edit", "callback_data": "email:edit"},
                ],
                [
                    {"text": "❌ Cancel", "callback_data": "email:cancel"},
                ],
            ]
        elif gmail_enabled and not to:
            text += "\n\n_Recipient missing — tell me who to send it to and I'll re-draft._"
            buttons = [[{"text": "❌ Cancel", "callback_data": "email:cancel"}]]
        else:
            text += "\n\n_Gmail isn't connected, so this is draft-only._"
            buttons = [[{"text": "❌ Discard", "callback_data": "email:cancel"}]]

        return SkillResponse(
            text=text,
            inline_buttons=buttons,
            metadata={"skill": self.name, "draft": draft},
        )
