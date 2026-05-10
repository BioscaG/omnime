"""Fetch a public URL, extract its text content, summarise + index.

Activated by /fetch <url> or by detecting a URL in a natural message.
Reuses the document analyzer for classification + chunked indexing.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import httpx

from src.skills.base import BaseSkill, SkillResponse
from src.skills.document_analyzer import DocumentAnalyzer, chunk_text

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)


SAFE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OMNIME/1.0; +https://github.com/) "
        "TelegramBot/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en,es;q=0.9",
}


class WebFetchSkill(BaseSkill):
    name = "web_fetch"
    description = "Read a public URL: extract its content, summarise it, and index relevant facts in memory."
    triggers = ["/fetch", "/url", "/scrape", "read this link", "lee esta web", "abre la url"]
    examples = [
        "aquí tienes mi web https://guidobiosca.com saca info y guarda lo relevante",
        "read this article and tell me the gist: https://example.com/post",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Public URL to fetch (must start with http:// or https://).",
            },
            "save_to_memory": {
                "type": "boolean",
                "description": "If true, run the entity extractor and store relevant facts about the user (default true).",
                "default": True,
            },
        },
        "required": ["url"],
    }

    MAX_BYTES = 4 * 1024 * 1024  # 4 MB cap on download
    TIMEOUT = 20.0

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower().strip()
        if m.startswith(("/fetch", "/url", "/scrape")):
            return 0.95
        if any(t in m for t in ("read this link", "lee esta web", "abre la url")) and URL_RE.search(message):
            return 0.85
        # Bare URL on its own line → likely a fetch request
        if URL_RE.match(message.strip()):
            return 0.7
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        url = self._extract_url(message)
        if not url:
            return SkillResponse(text="Pass me a public URL (https://...).")

        try:
            html = await self._fetch(url)
        except Exception as exc:
            return SkillResponse(text=f"Couldn't download {url}: {exc}")

        text = self._extract_text(html)
        if not text.strip():
            return SkillResponse(text=f"Page returned no usable text: {url}")

        # Classify + summarise.
        analyzer = DocumentAnalyzer(self.llm)
        analysis = await analyzer.analyze(text)

        # Index into the documents collection in chunks.
        chunks = chunk_text(text, target_chars=1800, overlap=200)
        for i, chunk in enumerate(chunks):
            try:
                self.memory.semantic.add(
                    collection="documents",
                    text=chunk,
                    metadata={
                        "user_id": context.user_id,
                        "source": "web",
                        "url": url,
                        "category": analysis.category,
                        "title": analysis.title or url,
                        "chunk": i,
                        "of": len(chunks),
                    },
                )
            except Exception as exc:
                logger.warning("Web chunk %d indexing failed: %s", i, exc)

        # Optionally extract personal entities if the page is about the user.
        extraction_summary = ""
        if analysis.should_extract_personal:
            try:
                result = await self.memory.process_and_store(
                    user_id=context.user_id,
                    message=text[:8000],
                    context_hint=f"Content fetched from {url}, classified as '{analysis.category}'.",
                )
                extraction_summary = result.stored_summary
            except Exception as exc:
                logger.warning("Personal extraction from URL failed: %s", exc)

        bullets: list[str] = [
            f"🌐 **{analysis.title or url}**",
            f"`{url}` · {analysis.category} · {len(chunks)} chunk(s) indexed",
        ]
        if analysis.tags:
            bullets.append(f"Tags: {', '.join(analysis.tags)}")
        if analysis.summary:
            bullets.append(f"\n{analysis.summary}")
        if analysis.key_entities:
            bullets.append(f"\n_Mentioned: {', '.join(analysis.key_entities[:6])}_")
        if extraction_summary and extraction_summary != "nothing new":
            bullets.append(f"\n✅ Added to your profile: {extraction_summary}")

        return SkillResponse(
            text="\n".join(bullets),
            metadata={"url": url, "category": analysis.category, "chunks": len(chunks)},
        )

    async def _fetch(self, url: str) -> str:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=self.TIMEOUT, headers=SAFE_HEADERS
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                ctype = response.headers.get("content-type", "")
                if "text" not in ctype and "html" not in ctype and "json" not in ctype:
                    raise ValueError(f"Unsupported content-type: {ctype}")
                buf = bytearray()
                async for chunk in response.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > self.MAX_BYTES:
                        raise ValueError(
                            f"Page too large (>{self.MAX_BYTES // (1024 * 1024)} MB)"
                        )
                # Honour declared encoding when available.
                encoding = response.encoding or "utf-8"
                return buf.decode(encoding, errors="replace")

    @staticmethod
    def _extract_text(html: str) -> str:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "noscript", "iframe"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            # Collapse whitespace.
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("HTML parse failed: %s", exc)
            return html

    @staticmethod
    def _extract_url(message: str) -> str | None:
        m = URL_RE.search(message)
        return m.group(0) if m else None
