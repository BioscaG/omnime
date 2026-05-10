"""Web primitives — fetch and search the live web.

These wrap the existing ``WebFetchSkill`` / ``WebResearcherSkill`` logic
but expose them as atomic tools that return structured data. The agent
composes summarisation/saving on top.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

import httpx

from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


SAFE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OMNIME/1.0; +https://github.com/) TelegramBot/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en,es;q=0.9",
}
URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)


async def _web_fetch(args: dict, context: "Context") -> str:
    url = (args.get("url") or "").strip()
    if not url:
        return json.dumps({"error": "url is required"})
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "url must start with http:// or https://"})
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=SAFE_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        return json.dumps({"error": f"fetch failed: {exc}"})

    text = _strip_html(html)
    title = _extract_title(html)
    return json.dumps({
        "url": url,
        "title": title,
        "text": text[:6000],
        "length": len(text),
    }, ensure_ascii=False)


WEB_FETCH = Tool(
    name="web_fetch",
    description=(
        "Fetch a public URL and return its title + main text (up to 6000 chars). "
        "After fetching, if the content contains facts about the user "
        "(projects, decisions, contacts, achievements, ideas), pass the FULL "
        "fetched text — not a summary — to memory_save so the entity "
        "extractor catches every detail. You can split a long page into "
        "multiple memory_save calls (per project / per section); the "
        "extractor dedups."
    ),
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    run=_web_fetch,
)


async def _web_search(args: dict, context: "Context") -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    n = int(args.get("n") or 5)
    try:
        # Lazy import — duckduckgo_search is heavy and only used here.
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=n))
    except Exception as exc:
        logger.warning("web_search failed: %s", exc)
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "query": query,
        "count": len(raw),
        "results": [
            {
                "title": r.get("title"),
                "url": r.get("href") or r.get("url"),
                "snippet": r.get("body") or r.get("snippet"),
            }
            for r in raw
        ],
    }, ensure_ascii=False)


WEB_SEARCH = Tool(
    name="web_search",
    description=(
        "Search the web (DuckDuckGo). Returns titles, URLs, and snippets. "
        "After searching, use web_fetch to read interesting results in full."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "n": {"type": "integer", "default": 5, "minimum": 1, "maximum": 15},
        },
        "required": ["query"],
    },
    run=_web_search,
)


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.S | re.I)
    return m.group(1).strip() if m else ""


def build_web_tools() -> list[Tool]:
    return [WEB_FETCH, WEB_SEARCH]
