"""Google Drive primitives — workspace folder visible from any device.

The agent can drop files into the user's Drive ('OMNIME' folder) so they
appear in the Drive app on phone/desktop instantly. Combined with
``chat_send_file`` this gives the user two ways to grab a bot-created
artifact: as a Telegram attachment OR by opening it in their Drive.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import settings
from src.integrations.drive_client import DriveClient
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


def _client_or_disabled() -> tuple[DriveClient | None, str | None]:
    try:
        c = DriveClient()
    except Exception as exc:
        return None, f"Drive integration error: {exc}"
    if not c.enabled:
        return None, "Google Drive isn't configured (GDRIVE_* env vars missing)."
    return c, None


def _resolve_local_file(arg: dict, user_id: int) -> tuple[Path | None, str | None, str | None]:
    """Resolve to a local Path the agent wants to upload. Accepts:
    - filename (string from data/uploads/)
    - file_record_id (FileRecord.id) → looked up to get the on-disk path
    Returns (path, suggested_name, error)."""
    fid = arg.get("file_record_id")
    if fid is not None:
        from sqlalchemy import select
        from src.memory import models as m
        from src.memory.db import session_scope

        with session_scope() as s:
            row = s.execute(
                select(m.FileRecord)
                .where(m.FileRecord.id == int(fid))
                .where(m.FileRecord.user_id == user_id)
            ).scalar_one_or_none()
            if row is None:
                return None, None, f"file_record_id {fid} not found"
            local = Path(settings.uploads_dir) / (row.filename or "")
            return (local if local.exists() else None,
                    row.filename, None if local.exists() else "file missing on disk")

    name = arg.get("filename")
    if name:
        p = Path(settings.uploads_dir) / name
        return (p if p.exists() else None, name, None if p.exists() else f"{name} not found")
    return None, None, "either file_record_id or filename is required"


async def _drive_upload(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    user_id = int(getattr(context, "user_id", 0) or 0)
    path, name, perr = _resolve_local_file(args, user_id)
    if perr or path is None:
        return json.dumps({"error": perr or "file resolution failed"})
    description = args.get("description")
    try:
        result = client.upload(path, remote_name=args.get("remote_name") or name, description=description)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "status": "uploaded",
        "id": result.get("id"),
        "name": result.get("name"),
        "webViewLink": result.get("webViewLink"),
        "size": result.get("size"),
        "mimeType": result.get("mimeType"),
    }, ensure_ascii=False)


DRIVE_UPLOAD = Tool(
    name="drive_upload",
    description=(
        "Upload a previously-stored file to the user's Google Drive "
        "OMNIME folder so they can access it from phone/desktop. Resolve "
        "the file by file_record_id (preferred — from files_list / "
        "files_search) or by filename. Returns the Drive URL."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_record_id": {"type": "integer", "description": "Preferred: id from files_list/files_search."},
            "filename": {"type": "string", "description": "Alternative: filename inside data/uploads/."},
            "remote_name": {"type": "string", "description": "Optional: override the name on Drive."},
            "description": {"type": "string"},
        },
        "required": [],
    },
    run=_drive_upload,
)


async def _drive_list(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    try:
        files = client.list_files(
            max_results=int(args.get("max_results") or 30),
            query=args.get("query") or None,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "count": len(files),
        "files": files,
    }, ensure_ascii=False, default=str)


DRIVE_LIST = Tool(
    name="drive_list",
    description="List files inside the user's OMNIME Drive folder. Optionally filter by name substring.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 30, "minimum": 1, "maximum": 100},
        },
        "required": [],
    },
    run=_drive_list,
)


async def _drive_share(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    fid = (args.get("id") or "").strip()
    if not fid:
        return json.dumps({"error": "id is required"})
    try:
        link = client.share_link(fid)
        return json.dumps({"id": fid, "shareable_link": link})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


DRIVE_SHARE = Tool(
    name="drive_share_link",
    description=(
        "Make a Drive file viewable by anyone with the link. Use ONLY when "
        "the user explicitly asks for a shareable link — anyone with the "
        "URL gets read access."
    ),
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
    run=_drive_share,
)


def build_drive_tools() -> list[Tool]:
    try:
        c = DriveClient()
        if not c.enabled:
            return []
    except Exception:
        return []
    return [DRIVE_UPLOAD, DRIVE_LIST, DRIVE_SHARE]
