"""Thin Notion REST wrapper for the OMNIME Notion sync.

Disabled when ``NOTION_TOKEN`` is missing. Uses the public Notion HTTP API
directly via httpx so we don't add another SDK dependency.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

import httpx

from src.config import settings


logger = logging.getLogger(__name__)


NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"


class NotionClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.notion_token
        self.enabled = bool(self.token)
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if not self.enabled:
            raise RuntimeError("Notion not configured")
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def query_database(
        self,
        database_id: str,
        filter_: dict[str, Any] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        client = await self._http()
        body: dict[str, Any] = {"page_size": page_size}
        if filter_:
            body["filter"] = filter_
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload = {**body}
            if cursor:
                payload["start_cursor"] = cursor
            r = await client.post(f"/databases/{database_id}/query", json=payload)
            r.raise_for_status()
            data = r.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return results

    async def upsert_page(
        self,
        database_id: str,
        properties: dict[str, Any],
        external_id: str,
    ) -> dict[str, Any]:
        """Idempotent upsert keyed by an external_id stored in a rich_text property."""
        existing = await self.query_database(
            database_id,
            filter_={
                "property": "external_id",
                "rich_text": {"equals": external_id},
            },
            page_size=1,
        )
        properties = {**properties, "external_id": _rich_text(external_id)}
        client = await self._http()
        if existing:
            page_id = existing[0]["id"]
            r = await client.patch(f"/pages/{page_id}", json={"properties": properties})
            r.raise_for_status()
            return r.json()
        r = await client.post(
            "/pages",
            json={
                "parent": {"database_id": database_id},
                "properties": properties,
            },
        )
        r.raise_for_status()
        return r.json()

    async def archive_pages(self, page_ids: Iterable[str]) -> None:
        client = await self._http()
        for pid in page_ids:
            r = await client.patch(f"/pages/{pid}", json={"archived": True})
            if r.status_code >= 400:
                logger.warning("Notion archive failed for %s: %s", pid, r.text)

    async def search(self, query: str, page_size: int = 10) -> list[dict[str, Any]]:
        """Search across the Notion workspace (pages + databases)."""
        client = await self._http()
        r = await client.post("/search", json={"query": query, "page_size": page_size})
        r.raise_for_status()
        return r.json().get("results", [])

    async def get_page(self, page_id: str) -> dict[str, Any]:
        client = await self._http()
        r = await client.get(f"/pages/{page_id}")
        r.raise_for_status()
        return r.json()

    async def get_block_children(self, block_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        """Walk the block tree under a page to extract its rendered text."""
        client = await self._http()
        r = await client.get(f"/blocks/{block_id}/children", params={"page_size": page_size})
        r.raise_for_status()
        return r.json().get("results", [])

    async def page_text(self, page_id: str, max_blocks: int = 80) -> str:
        """Return a flat plain-text rendering of a page's body."""
        try:
            blocks = await self.get_block_children(page_id, page_size=max_blocks)
        except Exception as exc:
            logger.warning("get_block_children failed for %s: %s", page_id, exc)
            return ""
        out: list[str] = []
        for b in blocks[:max_blocks]:
            t = b.get("type")
            payload = b.get(t) or {}
            chunks = payload.get("rich_text") or []
            text = "".join(c.get("plain_text") or "" for c in chunks)
            if not text:
                continue
            if t in ("heading_1", "heading_2", "heading_3"):
                out.append(f"# {text}")
            elif t == "bulleted_list_item":
                out.append(f"- {text}")
            elif t == "numbered_list_item":
                out.append(f"1. {text}")
            elif t == "to_do":
                checked = payload.get("checked")
                marker = "[x]" if checked else "[ ]"
                out.append(f"{marker} {text}")
            else:
                out.append(text)
        return "\n".join(out)

    async def create_page(self, parent_page_id: str, title_value: str, body: str | None = None) -> dict[str, Any]:
        client = await self._http()
        properties = {
            "title": {"title": [{"type": "text", "text": {"content": title_value}}]},
        }
        children = []
        if body:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": body}}],
                },
            })
        r = await client.post(
            "/pages",
            json={
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "properties": properties,
                "children": children,
            },
        )
        r.raise_for_status()
        return r.json()


def _rich_text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def title(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": value}}]}


def rich_text(value: str | None) -> dict[str, Any]:
    return _rich_text(value or "")


def select(value: str | None) -> dict[str, Any]:
    return {"select": ({"name": value} if value else None)}


def multi_select(values: list[str] | None) -> dict[str, Any]:
    return {"multi_select": [{"name": v} for v in (values or []) if v]}


def number(value: float | None) -> dict[str, Any]:
    return {"number": value}


def date_property(value: str | None) -> dict[str, Any]:
    return {"date": ({"start": value} if value else None)}
