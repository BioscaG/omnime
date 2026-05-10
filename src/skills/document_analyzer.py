"""Analyse uploaded documents: classify type, summarise, extract entities,
chunk for better semantic indexing.

Used internally by the document handler — not exposed as a slash command.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.brain.llm_client import LLMClient


logger = logging.getLogger(__name__)


CLASSIFY_PROMPT = """You receive the first ~3000 characters of a document. Classify and summarise.

Reply STRICTLY with JSON:
{{
  "category": "cv" | "cover_letter" | "contract" | "invoice" | "receipt" | "paper" |
              "book_chapter" | "report" | "presentation" | "notes" | "code" |
              "transcript" | "email" | "meeting_minutes" | "other",
  "title": "<a short title for the doc>",
  "summary": "<2-3 sentence factual summary>",
  "tags": ["3-5 relevant tags"],
  "language": "<ISO 639-1 code: en, es, ...>",
  "key_entities": ["names of people / companies / projects mentioned"],
  "should_extract_personal": true | false
}}

`should_extract_personal` is true ONLY when the document is clearly about the user
themselves (their CV, their cover letter, notes about their own life, their journal).

Document excerpt:
\"\"\"
{excerpt}
\"\"\"
"""


@dataclass
class DocAnalysis:
    category: str = "other"
    title: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    language: str = ""
    key_entities: list[str] = field(default_factory=list)
    should_extract_personal: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class DocumentAnalyzer:
    def __init__(self, llm: "LLMClient") -> None:
        self.llm = llm

    async def analyze(self, text: str) -> DocAnalysis:
        if not text.strip():
            return DocAnalysis()
        excerpt = text[:3000]
        try:
            raw = await self.llm.complete(
                prompt=CLASSIFY_PROMPT.format(excerpt=excerpt),
                system="You are a document classifier. Reply with valid JSON only.",
                model_tier="tiny",
                max_tokens=600,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning("Document classification failed: %s", exc)
            return DocAnalysis()

        data = self._parse_json(raw)
        if not data:
            return DocAnalysis()
        return DocAnalysis(
            category=str(data.get("category", "other")),
            title=str(data.get("title") or "")[:200],
            summary=str(data.get("summary") or "")[:1500],
            tags=[t for t in (data.get("tags") or []) if isinstance(t, str)][:8],
            language=str(data.get("language") or "")[:5],
            key_entities=[e for e in (data.get("key_entities") or []) if isinstance(e, str)][:20],
            should_extract_personal=bool(data.get("should_extract_personal", False)),
            raw=data,
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        s = raw.strip()
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
        if m:
            s = m.group(1)
        try:
            return json.loads(s)
        except Exception as exc:
            logger.warning("Could not parse classifier JSON: %s\nraw=%s", exc, raw[:300])
            return None


def chunk_text(text: str, target_chars: int = 1800, overlap: int = 200) -> list[str]:
    """Split a long document into semantic-friendly chunks.

    Splits on paragraph boundaries when possible, falling back to fixed windows
    when paragraphs are too long. Adds a small overlap between consecutive chunks
    so meaning isn't lost across the cut.
    """
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text.strip())
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > target_chars:
            # Hard-split this paragraph by character window with overlap.
            for i in range(0, len(para), target_chars - overlap):
                chunks.append(para[i : i + target_chars])
            continue
        if len(buf) + len(para) + 2 > target_chars and buf:
            chunks.append(buf)
            buf = para
        else:
            buf = (buf + "\n\n" + para) if buf else para
    if buf:
        chunks.append(buf)
    return chunks
