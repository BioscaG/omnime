"""Files primitives — search, list, and read documents/photos the user has uploaded.

Lets the agent answer "¿qué decía el contrato del piso?" by searching the
indexed file corpus and pulling the relevant excerpt instead of asking the
user to remind it. Also categorises the corpus so the user (or model) can
browse by kind: contracts, invoices, CVs, papers, screenshots, etc.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.memory import models as m
from src.memory.db import session_scope
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


async def _files_list(args: dict, context: "Context") -> str:
    user_id = int(getattr(context, "user_id", 0) or 0)
    category = (args.get("category") or "").strip().lower()
    limit = int(args.get("limit") or 30)
    with session_scope() as s:
        stmt = (
            select(m.FileRecord)
            .where(m.FileRecord.user_id == user_id)
            .order_by(m.FileRecord.created_at.desc())
            .limit(limit)
        )
        rows = s.execute(stmt).scalars().all()
        if category:
            rows = [
                r for r in rows
                if (r.extra_metadata or {}).get("category", "").lower() == category
            ]
        return json.dumps({
            "count": len(rows),
            "files": [
                {
                    "id": r.id,
                    "filename": r.filename,
                    "file_type": r.file_type,
                    "category": (r.extra_metadata or {}).get("category"),
                    "title": (r.extra_metadata or {}).get("title"),
                    "summary": (r.summary or "")[:300],
                    "tags": r.tags or [],
                    "uploaded": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }, ensure_ascii=False)


FILES_LIST = Tool(
    name="files_list",
    description=(
        "List the user's uploaded documents and photos, most recent first. "
        "Filter by category ('contract', 'invoice', 'cv', 'paper', 'image', "
        "'receipt', 'other')."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
        },
        "required": [],
    },
    run=_files_list,
)


async def _files_search(args: dict, context: "Context") -> str:
    """Semantic search across the user's indexed file chunks."""
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    n = int(args.get("n") or 6)
    user_id = int(getattr(context, "user_id", 0) or 0)
    try:
        from src.skills.registry import get_registry

        memory = get_registry().memory
        hits = memory.semantic.search(
            collection="documents",
            query=query,
            n_results=n,
            where={"user_id": user_id},
        )
    except Exception as exc:
        logger.warning("files_search failed: %s", exc)
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "query": query,
        "count": len(hits),
        "hits": [
            {
                "filename": (h.metadata or {}).get("filename"),
                "category": (h.metadata or {}).get("category"),
                "chunk": (h.metadata or {}).get("chunk"),
                "excerpt": (h.text or h.content if hasattr(h, "content") else "")[:600] if hasattr(h, "text") else "",
                "score": getattr(h, "score", None),
            }
            for h in hits
        ],
    }, ensure_ascii=False, default=lambda o: str(o))


FILES_SEARCH = Tool(
    name="files_search",
    description=(
        "Semantic search across the user's uploaded documents and photo "
        "descriptions. Returns matching excerpts with filename + category. "
        "Use when the user asks 'qué decía el contrato', 'cuándo es la "
        "siguiente factura', 'dónde apunté X' — anything that's likely in "
        "an uploaded file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "n": {"type": "integer", "default": 6, "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    },
    run=_files_search,
)


async def _files_get(args: dict, context: "Context") -> str:
    """Get full record for one file by id, including summary + extracted text."""
    fid = args.get("id")
    if fid is None:
        return json.dumps({"error": "id is required"})
    user_id = int(getattr(context, "user_id", 0) or 0)
    with session_scope() as s:
        r = s.execute(
            select(m.FileRecord).where(m.FileRecord.id == int(fid)).where(m.FileRecord.user_id == user_id)
        ).scalar_one_or_none()
        if r is None:
            return json.dumps({"error": f"file {fid} not found"})
        return json.dumps({
            "id": r.id,
            "filename": r.filename,
            "file_type": r.file_type,
            "summary": r.summary,
            "tags": r.tags or [],
            "metadata": r.extra_metadata or {},
            "extracted_text": (r.extracted_text or "")[:8000],
            "uploaded": r.created_at.isoformat() if r.created_at else None,
        }, ensure_ascii=False)


FILES_GET = Tool(
    name="files_get",
    description="Fetch the full record (summary + extracted text + metadata) of a single uploaded file by id. Use after files_list / files_search to drill into one item.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    },
    run=_files_get,
)


async def _files_delete(args: dict, context: "Context") -> str:
    fid = args.get("id")
    if fid is None:
        return json.dumps({"error": "id is required"})
    user_id = int(getattr(context, "user_id", 0) or 0)
    try:
        from pathlib import Path
        from src.config import settings as _settings
        from src.skills.registry import get_registry

        with session_scope() as s:
            r = s.execute(
                select(m.FileRecord)
                .where(m.FileRecord.id == int(fid))
                .where(m.FileRecord.user_id == user_id)
            ).scalar_one_or_none()
            if r is None:
                return json.dumps({"error": f"file {fid} not found"})

            filename = r.filename
            metadata = dict(r.extra_metadata or {})
            metadata["filename"] = filename

            # Drop ChromaDB chunks for this file.
            try:
                memory = get_registry().memory
                memory.semantic.delete_where(
                    collection="documents",
                    where={"user_id": user_id, "filename": filename or ""},
                )
            except Exception as exc:
                logger.warning("ChromaDB chunk delete failed for %s: %s", filename, exc)

            # Drop the file from disk if it exists.
            try:
                disk_path = Path(_settings.uploads_dir) / (filename or "")
                if disk_path.exists():
                    disk_path.unlink()
            except Exception as exc:
                logger.warning("Disk file delete failed for %s: %s", filename, exc)

            s.delete(r)

            # Audit row.
            try:
                s.add(m.AuditLog(
                    user_id=user_id,
                    action="delete",
                    entity_type="file",
                    entity_id=int(fid),
                    details={"filename": filename, **metadata},
                ))
            except Exception as exc:
                logger.debug("audit log write failed: %s", exc)

        return json.dumps({"status": "deleted", "id": int(fid), "filename": filename})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


FILES_DELETE = Tool(
    name="files_delete",
    description=(
        "Delete an uploaded file by id — removes the FileRecord, the "
        "ChromaDB chunks, and the file from disk. Audit-logged. ONLY "
        "call this when the user EXPLICITLY asks to delete the file "
        "('elimina', 'borra', 'delete'); never as a side effect of "
        "another instruction."
    ),
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    },
    run=_files_delete,
)


def build_files_tools() -> list[Tool]:
    return [FILES_LIST, FILES_SEARCH, FILES_GET, FILES_DELETE]
