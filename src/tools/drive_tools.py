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


async def _drive_download(args: dict, context: "Context") -> str:
    """Pull a Drive file into data/uploads/ and register a FileRecord
    so it shows up in /files and is usable by claude_code_analyze /
    chat_send_file / files_search."""
    client, err = _client_or_disabled()
    if err:
        return err
    drive_id = (args.get("id") or "").strip()
    if not drive_id:
        return json.dumps({"error": "id is required"})

    user_id = int(getattr(context, "user_id", 0) or 0)
    save_as = (args.get("save_as") or "").strip()

    try:
        meta = client.get_metadata(drive_id)
    except Exception as exc:
        return json.dumps({"error": f"metadata fetch failed: {exc}"})
    suggested = save_as or meta.get("name") or f"drive_{drive_id}.bin"

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    target = Path(settings.uploads_dir) / suggested
    # Avoid collision: append numeric suffix if needed.
    base, dot, ext = target.name.partition(".")
    i = 2
    while target.exists():
        target = target.parent / f"{base}_{i}.{ext}" if ext else target.parent / f"{base}_{i}"
        i += 1

    try:
        actual = client.download(drive_id, target)
    except Exception as exc:
        return json.dumps({"error": f"download failed: {exc}"})

    # Persist a FileRecord. Reuses the upsert dedup if telegram_file_id
    # matches some prior upload (unlikely here but harmless).
    from src.memory.db import session_scope
    from src.memory.structured import StructuredStore

    with session_scope() as s:
        stored = StructuredStore(s).add_file(
            user_id=user_id,
            filename=actual.name,
            file_type=meta.get("mimeType") or "application/octet-stream",
            telegram_file_id=None,
            extracted_text=None,
            summary=f"Downloaded from Drive: {meta.get('name')}",
            tags=["drive-download"],
            extra_metadata={
                "category": "drive",
                "drive_id": drive_id,
                "drive_url": meta.get("webViewLink"),
                "size": meta.get("size"),
                "title": meta.get("name"),
            },
        )
        record_id = stored.id

    return json.dumps({
        "status": "downloaded",
        "file_record_id": record_id,
        "filename": actual.name,
        "path": str(actual),
        "size_kb": actual.stat().st_size // 1024,
        "drive_url": meta.get("webViewLink"),
    }, ensure_ascii=False)


DRIVE_DOWNLOAD = Tool(
    name="drive_download",
    description=(
        "Download a Drive file into data/uploads/ and register it as a "
        "regular FileRecord so it can be opened with files_get, "
        "analysed with claude_code_analyze, or sent to the user with "
        "chat_send_file. Auto-exports Google natives (Docs → PDF, "
        "Sheets → CSV, Slides → PDF). Returns the new file_record_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Drive file id, from drive_list / drive_find."},
            "save_as": {"type": "string", "description": "Optional override for the local filename."},
        },
        "required": ["id"],
    },
    run=_drive_download,
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


async def _drive_create_folder(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    name = (args.get("name") or "").strip()
    if not name:
        return json.dumps({"error": "name is required"})
    parent_id = (args.get("parent_id") or "").strip() or None
    try:
        result = client.create_folder(name=name, parent_id=parent_id)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "status": "created",
        "id": result.get("id"),
        "name": result.get("name"),
        "webViewLink": result.get("webViewLink"),
    }, ensure_ascii=False)


DRIVE_CREATE_FOLDER = Tool(
    name="drive_create_folder",
    description=(
        "Create a folder in Drive. Without parent_id, creates inside the "
        "OMNIME workspace folder. To create a sub-sub-folder, first call "
        "drive_list / drive_find to resolve the parent_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "parent_id": {"type": "string", "description": "Optional. Drive id of the parent folder. Defaults to OMNIME workspace folder."},
        },
        "required": ["name"],
    },
    run=_drive_create_folder,
)


async def _drive_move(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    fid = (args.get("id") or "").strip()
    if not fid:
        return json.dumps({"error": "id is required"})
    try:
        result = client.move(
            file_id=fid,
            new_parent_id=(args.get("new_parent_id") or "").strip() or None,
            new_name=(args.get("new_name") or "").strip() or None,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "status": "moved",
        "id": result.get("id"),
        "name": result.get("name"),
        "parents": result.get("parents"),
        "webViewLink": result.get("webViewLink"),
    }, ensure_ascii=False)


DRIVE_MOVE = Tool(
    name="drive_move",
    description=(
        "Move a Drive file or folder to a different parent and/or rename "
        "it. Pass id (the thing to move), and either new_parent_id, "
        "new_name, or both."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Drive id of the file/folder being moved."},
            "new_parent_id": {"type": "string"},
            "new_name": {"type": "string"},
        },
        "required": ["id"],
    },
    run=_drive_move,
)


async def _drive_find(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    name = (args.get("name") or "").strip()
    if not name:
        return json.dumps({"error": "name is required"})
    try:
        items = client.find_by_name(
            name=name,
            parent_id=(args.get("parent_id") or "").strip() or None,
            only_folders=bool(args.get("only_folders", False)),
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"count": len(items), "items": items}, ensure_ascii=False)


DRIVE_FIND = Tool(
    name="drive_find",
    description=(
        "Find a Drive file/folder by exact name in a parent folder. Use "
        "to resolve a folder name → id before calling drive_create_folder "
        "with parent_id, or drive_move with new_parent_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "parent_id": {"type": "string", "description": "Optional. Defaults to OMNIME workspace."},
            "only_folders": {"type": "boolean", "default": False},
        },
        "required": ["name"],
    },
    run=_drive_find,
)


async def _drive_delete(args: dict, context: "Context") -> str:
    client, err = _client_or_disabled()
    if err:
        return err
    fid = (args.get("id") or "").strip()
    if not fid:
        return json.dumps({"error": "id is required"})
    try:
        client.delete(fid)
        return json.dumps({"status": "deleted", "id": fid})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


DRIVE_DELETE = Tool(
    name="drive_delete",
    description=(
        "Delete a Drive file or folder by id. ONLY call when the user "
        "explicitly asks ('borra', 'elimina'); never as a side effect. "
        "Folders are deleted with all their contents."
    ),
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
    run=_drive_delete,
)


def build_drive_tools() -> list[Tool]:
    try:
        c = DriveClient()
        if not c.enabled:
            return []
    except Exception:
        return []
    return [
        DRIVE_UPLOAD, DRIVE_DOWNLOAD, DRIVE_LIST, DRIVE_FIND,
        DRIVE_CREATE_FOLDER, DRIVE_MOVE, DRIVE_DELETE,
        DRIVE_SHARE,
    ]
