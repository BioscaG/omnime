"""Reading list: list, summarise, and capture takeaways from books."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


class ReadingListSkill(BaseSkill):
    name = "reading_list"
    description = "Show your reading list with status and key takeaways."
    triggers = ["/books", "/reading", "reading list", "what am I reading", "books I've read"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/books") or m.startswith("/reading"):
            return 0.95
        if "reading list" in m or "what am i reading" in m:
            return 0.8
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        status = self._parse_status(message)
        with session_scope() as s:
            books = StructuredStore(s).list_books(context.user_id, status=status)

        if not books:
            return SkillResponse(text="No books recorded yet.")

        sections: dict[str, list[str]] = {}
        for b in books:
            bucket = (b.status or "unknown").title()
            entry = f"• **{b.title}**" + (f" — {b.author}" if b.author else "")
            if b.rating is not None:
                entry += f" · ⭐ {b.rating:.1f}"
            takeaways = b.takeaways or []
            if takeaways:
                entry += "\n   " + "; ".join(t[:120] for t in takeaways[:3])
            sections.setdefault(bucket, []).append(entry)

        lines: list[str] = []
        for bucket in ("Reading", "Wishlist", "Finished", "Abandoned", "Unknown"):
            if bucket in sections:
                lines.append(f"📚 *{bucket}*")
                lines.extend(sections[bucket])
                lines.append("")
        return SkillResponse(text="\n".join(lines).strip())

    @staticmethod
    def _parse_status(message: str) -> str | None:
        text = message.lower()
        for status in ("reading", "wishlist", "finished", "abandoned"):
            if status in text:
                return status
        return None
