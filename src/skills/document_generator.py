"""Generic document generator: one-pagers, briefs, reports, posts."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import settings
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


DOC_PROMPT = """Produce a polished {doc_type} based on the user's request.

User profile:
{profile}

Relevant context:
{context}

Request:
{request}

Output Markdown. Use clear section headings. Be concrete and grounded in the context above.
Do not invent facts that aren't supported."""


class DocumentGeneratorSkill(BaseSkill):
    name = "document_generator"
    description = "Generate a one-pager, brief, proposal, report or LinkedIn post."
    triggers = [
        "one-pager", "one pager", "brief", "proposal", "report",
        "linkedin post", "blog post", "write a doc", "generate a document",
        "summary doc", "executive summary",
    ]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        score = super().can_handle(message, intent)
        if any(k in m for k in ("write a", "generate a", "draft a")) and any(
            k in m for k in ("doc", "post", "brief", "proposal", "report", "summary", "one-pager")
        ):
            score = max(score, 0.85)
        return score

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        doc_type = self._infer_doc_type(message)
        profile = context.profile or {}
        body = await self.llm.complete(
            prompt=DOC_PROMPT.format(
                doc_type=doc_type,
                profile=profile.get("living_profile") or profile.get("bio") or "(unknown)",
                context=context.to_prompt_block()[:3000],
                request=message,
            ),
            system="You are a senior technical writer.",
            model_tier="powerful",
            max_tokens=2000,
        )

        export_dir = settings.exports_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "_", doc_type.lower()).strip("_") or "doc"
        path = export_dir / f"{slug}_{ts}.md"
        path.write_text(body, encoding="utf-8")

        return SkillResponse(
            text=f"{doc_type.title()} draft saved.\n\n{body[:1500]}{'...' if len(body) > 1500 else ''}",
            files=[{"path": str(path), "type": "markdown", "name": path.name}],
            metadata={"doc_type": doc_type},
        )

    @staticmethod
    def _infer_doc_type(message: str) -> str:
        m = message.lower()
        for k in (
            "one-pager", "linkedin post", "blog post", "executive summary",
            "proposal", "report", "brief",
        ):
            if k in m:
                return k
        return "document"
