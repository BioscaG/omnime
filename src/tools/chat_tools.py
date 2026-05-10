"""Chat-side primitives — letting the agent send things directly to the
user's Telegram (files, photos) instead of just text.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.config import settings
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


def _resolve_local_file(arg: dict, user_id: int) -> tuple[Path | None, str | None, str | None]:
    """Same resolution as drive_tools — resolve file_record_id or filename
    to a Path inside data/uploads/."""
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


async def _chat_send_file(args: dict, context: "Context") -> str:
    """Send a file as a Telegram document attachment in the user's chat."""
    user_id = int(getattr(context, "user_id", 0) or 0)
    path, name, err = _resolve_local_file(args, user_id)
    if err or path is None:
        return json.dumps({"error": err or "file resolution failed"})

    chat_id = settings.telegram_user_id or settings.proactive_chat_id
    if not chat_id:
        return json.dumps({"error": "no chat_id configured (TELEGRAM_USER_ID missing)"})

    # Pull the bot Application from a process-level registry. Set at boot
    # time by src/bot/app.py so primitives can reach it without going
    # through the orchestrator init signature.
    from src.bot.runtime import get_application

    app = get_application()
    if app is None:
        return json.dumps({"error": "bot application not registered"})

    caption = args.get("caption") or ""
    try:
        with path.open("rb") as fh:
            await app.bot.send_document(
                chat_id=chat_id,
                document=fh,
                filename=name,
                caption=caption[:1000] or None,
            )
        return json.dumps({"status": "sent", "filename": name, "size": path.stat().st_size})
    except Exception as exc:
        logger.warning("chat_send_file failed: %s", exc)
        return json.dumps({"error": str(exc)})


CHAT_SEND_FILE = Tool(
    name="chat_send_file",
    description=(
        "Send a file as a Telegram document attachment. Use when the user "
        "asks 'mándame el archivo X' / 'pásame ese PDF' / 'send me Y'. "
        "Resolve via file_record_id (from files_list/files_search) or "
        "filename. Optional caption (1 line max)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_record_id": {"type": "integer"},
            "filename": {"type": "string"},
            "caption": {"type": "string"},
        },
        "required": [],
    },
    run=_chat_send_file,
)


def build_chat_tools() -> list[Tool]:
    return [CHAT_SEND_FILE]
