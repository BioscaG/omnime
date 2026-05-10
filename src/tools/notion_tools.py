"""Notion primitives — search the workspace, read a page, create a quick note.

Lets the agent treat Notion as a second memory: '¿qué tengo apuntado sobre
X en Notion?', 'crea una página con estos puntos en Notion'.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from src.integrations.notion_client import NotionClient
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


def _client_or_disabled() -> tuple[NotionClient | None, str | None]:
    try:
        c = NotionClient()
    except Exception as exc:
        return None, f"Notion error: {exc}"
    if not c.enabled:
        return None, "Notion isn't configured (NOTION_TOKEN missing)."
    return c, None


def _title_of(result: dict) -> str:
    """Extract the title string from a Notion page/database result."""
    obj = result.get("object")
    props = result.get("properties") or {}
    # Database-shaped results expose title as a property called "title" or
    # named whatever the schema says — pick the one with type=title.
    for key, val in props.items():
        if val and val.get("type") == "title":
            chunks = val.get("title") or []
            if chunks:
                return "".join(c.get("plain_text") or "" for c in chunks)
    if obj == "database":
        chunks = result.get("title") or []
        return "".join(c.get("plain_text") or "" for c in chunks)
    return "(untitled)"


async def _notion_search(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    n = int(args.get("n") or 10)
    try:
        results = await client.search(query, page_size=n)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    finally:
        await client.aclose()
    return json.dumps({
        "query": query,
        "count": len(results),
        "results": [
            {
                "id": r.get("id"),
                "object": r.get("object"),
                "title": _title_of(r),
                "url": r.get("url"),
                "last_edited_time": r.get("last_edited_time"),
            }
            for r in results
        ],
    }, ensure_ascii=False)


NOTION_SEARCH = Tool(
    name="notion_search",
    description=(
        "Search the user's Notion workspace. Returns matching pages and "
        "databases (id + title + URL). Use notion_read_page to fetch the "
        "actual content of an interesting result."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "n": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30},
        },
        "required": ["query"],
    },
    run=_notion_search,
)


async def _notion_read_page(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    page_id = (args.get("page_id") or "").strip()
    if not page_id:
        return json.dumps({"error": "page_id is required"})
    try:
        page = await client.get_page(page_id)
        body = await client.page_text(page_id)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    finally:
        await client.aclose()
    return json.dumps({
        "id": page.get("id"),
        "url": page.get("url"),
        "title": _title_of(page),
        "last_edited_time": page.get("last_edited_time"),
        "body": body[:6000],
    }, ensure_ascii=False)


NOTION_READ_PAGE = Tool(
    name="notion_read_page",
    description="Fetch a Notion page's title, URL, and rendered plain-text body. Use after notion_search.",
    input_schema={
        "type": "object",
        "properties": {"page_id": {"type": "string"}},
        "required": ["page_id"],
    },
    run=_notion_read_page,
)


async def _notion_create_quick_note(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    parent_id = (args.get("parent_page_id") or "").strip()
    title_value = (args.get("title") or "").strip()
    body = args.get("body") or ""
    if not parent_id or not title_value:
        return json.dumps({"error": "parent_page_id and title are required"})
    try:
        page = await client.create_page(parent_id, title_value, body)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    finally:
        await client.aclose()
    return json.dumps({
        "status": "created",
        "id": page.get("id"),
        "url": page.get("url"),
    }, ensure_ascii=False)


NOTION_CREATE_NOTE = Tool(
    name="notion_create_note",
    description=(
        "Create a new Notion page as a child of an existing parent page. "
        "Confirm the parent_page_id with the user (or look it up via "
        "notion_search) before calling — random parents lead to clutter."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "parent_page_id": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["parent_page_id", "title"],
    },
    run=_notion_create_quick_note,
)


def build_notion_tools() -> list[Tool]:
    try:
        c = NotionClient()
        if not c.enabled:
            return []
    except Exception:
        return []
    return [NOTION_SEARCH, NOTION_READ_PAGE, NOTION_CREATE_NOTE]
