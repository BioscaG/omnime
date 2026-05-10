"""Web research skill backed by DuckDuckGo (no API key required)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


SUMMARY_PROMPT = """Summarise the search results to answer the user's question.
Be concise, factual, and cite sources inline as [n].

Question: {question}

Results:
{results}
"""


class WebResearcherSkill(BaseSkill):
    name = "web_researcher"
    description = "Research a topic on the web and return a sourced summary."
    triggers = [
        "/research", "research", "look up", "search the web",
        "find information about", "what is", "who is",
        "investiga", "busca info sobre", "averigua",
    ]
    examples = [
        "investiga las últimas tendencias en small language models",
        "research the founders of Mistral AI",
    ]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/research"):
            return 0.95
        if any(t in m for t in ("research", "look up", "search the web", "find information about")):
            return 0.8
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        query = self._strip_command(message)
        results = self._search(query)
        if not results:
            return SkillResponse(text=f"Couldn't find anything for: {query}")

        formatted = "\n\n".join(
            f"[{i+1}] {r['title']}\n{r['href']}\n{r['body']}"
            for i, r in enumerate(results)
        )
        summary = await self.llm.complete(
            prompt=SUMMARY_PROMPT.format(question=query, results=formatted),
            system="You produce concise, source-cited summaries.",
            model_tier="fast",
            max_tokens=1000,
        )
        sources = "\n".join(f"[{i+1}] {r['href']}" for i, r in enumerate(results))
        return SkillResponse(
            text=f"{summary}\n\nSources:\n{sources}",
            metadata={"query": query, "n_results": len(results)},
        )

    def _search(self, query: str, max_results: int = 6) -> list[dict[str, str]]:
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        except Exception as exc:
            logger.warning("DuckDuckGo search failed: %s", exc)
            return []

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        if m.lower().startswith("/research"):
            parts = m.split(maxsplit=1)
            return parts[1].strip() if len(parts) > 1 else ""
        return m
