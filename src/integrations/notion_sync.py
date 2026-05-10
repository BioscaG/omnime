"""Bidirectional sync between OMNIME's structured store and Notion databases."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from src.config import settings
from src.integrations import notion_client as nc
from src.integrations.notion_client import NotionClient
from src.memory import models as m
from src.memory.db import session_scope


logger = logging.getLogger(__name__)


class NotionSync:
    def __init__(self, memory, client: NotionClient | None = None) -> None:
        self.memory = memory
        self.client = client or NotionClient()

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    # --- Push -----------------------------------------------------------
    async def push_all(self, user_id: int) -> dict[str, int]:
        if not self.enabled:
            return {"skipped": 1}
        stats = {"projects": 0, "ideas": 0, "contacts": 0}
        if settings.notion_projects_db:
            stats["projects"] = await self._push_projects(user_id)
        if settings.notion_ideas_db:
            stats["ideas"] = await self._push_ideas(user_id)
        if settings.notion_contacts_db:
            stats["contacts"] = await self._push_contacts(user_id)
        return stats

    async def _push_projects(self, user_id: int) -> int:
        with session_scope() as s:
            rows = list(s.scalars(select(m.Project).where(m.Project.user_id == user_id)))
            payload = []
            for p in rows:
                payload.append((str(p.id), {
                    "Name": nc.title(p.name),
                    "Status": nc.select(p.status),
                    "Role": nc.rich_text(p.role),
                    "Description": nc.rich_text(p.description),
                    "Technologies": nc.multi_select(p.technologies or []),
                    "Importance": nc.number(p.importance or 0.5),
                    "Start": nc.date_property(p.start_date.isoformat() if p.start_date else None),
                    "End": nc.date_property(p.end_date.isoformat() if p.end_date else None),
                }))
        for ext_id, props in payload:
            try:
                await self.client.upsert_page(
                    settings.notion_projects_db, properties=props, external_id=f"project:{ext_id}",
                )
            except Exception as exc:
                logger.warning("Notion project push failed: %s", exc)
        return len(payload)

    async def _push_ideas(self, user_id: int) -> int:
        with session_scope() as s:
            rows = list(s.scalars(select(m.Idea).where(m.Idea.user_id == user_id)))
            payload = []
            for i in rows:
                payload.append((str(i.id), {
                    "Name": nc.title((i.content or "")[:200]),
                    "Status": nc.select(i.status),
                    "Category": nc.select(i.category),
                    "Tags": nc.multi_select(i.tags or []),
                    "Importance": nc.number(i.importance or 0.5),
                }))
        for ext_id, props in payload:
            try:
                await self.client.upsert_page(
                    settings.notion_ideas_db, properties=props, external_id=f"idea:{ext_id}",
                )
            except Exception as exc:
                logger.warning("Notion idea push failed: %s", exc)
        return len(payload)

    async def _push_contacts(self, user_id: int) -> int:
        with session_scope() as s:
            rows = list(s.scalars(select(m.Contact).where(m.Contact.user_id == user_id)))
            payload = []
            for c in rows:
                payload.append((str(c.id), {
                    "Name": nc.title(c.name),
                    "Relationship": nc.select(c.relationship_type),
                    "Organization": nc.rich_text(c.organization),
                    "Email": nc.rich_text(c.email),
                    "Phone": nc.rich_text(c.phone),
                    "Notes": nc.rich_text(c.notes),
                }))
        for ext_id, props in payload:
            try:
                await self.client.upsert_page(
                    settings.notion_contacts_db, properties=props, external_id=f"contact:{ext_id}",
                )
            except Exception as exc:
                logger.warning("Notion contact push failed: %s", exc)
        return len(payload)

    # --- Pull -----------------------------------------------------------
    async def pull_ideas(self, user_id: int) -> int:
        """Pull new ideas authored on the Notion side back into OMNIME."""
        if not self.enabled or not settings.notion_ideas_db:
            return 0
        pages = await self.client.query_database(
            settings.notion_ideas_db,
            filter_={"property": "external_id", "rich_text": {"is_empty": True}},
        )
        added = 0
        with session_scope() as s:
            from src.memory.structured import StructuredStore

            store = StructuredStore(s)
            for page in pages:
                props = page.get("properties", {})
                content = _extract_title(props.get("Name", {})) or _extract_rich(props.get("Description", {}))
                if not content:
                    continue
                category = _extract_select(props.get("Category", {}))
                tags = _extract_multi(props.get("Tags", {}))
                store.add_idea(
                    user_id=user_id, content=content, category=category, tags=tags,
                )
                store.add_audit(user_id, "pull_notion", "idea", None, {"page_id": page.get("id")})
                added += 1
        return added


def _extract_title(prop: dict[str, Any]) -> str:
    items = prop.get("title", []) or []
    return "".join((i.get("plain_text") or "") for i in items)


def _extract_rich(prop: dict[str, Any]) -> str:
    items = prop.get("rich_text", []) or []
    return "".join((i.get("plain_text") or "") for i in items)


def _extract_select(prop: dict[str, Any]) -> str | None:
    sel = prop.get("select")
    return sel.get("name") if sel else None


def _extract_multi(prop: dict[str, Any]) -> list[str]:
    return [v.get("name") for v in prop.get("multi_select", []) or [] if v.get("name")]
