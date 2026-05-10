"""Top-level message handlers: text, voice, documents, photos, forwarded."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.bot.middleware import authorize, rate_limit
from src.config import settings
from src.utils.formatters import chunk, safe_send


logger = logging.getLogger(__name__)


# --- Whisper singleton -------------------------------------------------------

_whisper_model = None
_whisper_lock = asyncio.Lock()


async def _get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    async with _whisper_lock:
        if _whisper_model is None:
            def _load():
                import whisper
                return whisper.load_model("base")
            _whisper_model = await asyncio.to_thread(_load)
    return _whisper_model


async def _transcribe(path: Path) -> str:
    model = await _get_whisper_model()
    result = await asyncio.to_thread(model.transcribe, str(path))
    return (result.get("text") or "").strip()


# --- Sending -----------------------------------------------------------------

async def _send_response(update: Update, response) -> None:
    chat = update.effective_chat
    if chat is None:
        return

    text = response.text or ""
    if text:
        for piece in chunk(text, size=3500):
            await safe_send(chat.send_message, piece)

    if response.inline_buttons:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
            for row in response.inline_buttons
        ]
        await chat.send_message(
            "Choose:", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    for f in response.files or []:
        path = Path(f["path"])
        if path.exists():
            with path.open("rb") as fh:
                await chat.send_document(document=fh, filename=path.name)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not await rate_limit(update, context):
        return

    msg = update.effective_message
    if msg is None or msg.text is None:
        return

    chat = update.effective_chat
    await chat.send_chat_action(ChatAction.TYPING)

    orchestrator = context.application.bot_data["orchestrator"]
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]

    forwarded = _forwarded_origin(msg)
    incoming = msg.text
    if forwarded:
        incoming = f"[forwarded from {forwarded}]\n{incoming}"

    memory.log_message(
        user_id=user_id_db, text=incoming, role="user",
        telegram_message_id=msg.message_id,
    )

    response = await orchestrator.process_message(user_id=user_id_db, message=incoming)

    await _send_response(update, response)

    memory.log_message(
        user_id=user_id_db,
        text=response.text,
        role="assistant",
        intent=response.intent.value if response.intent else None,
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not await rate_limit(update, context):
        return

    msg = update.effective_message
    if msg is None or msg.voice is None:
        return

    chat = update.effective_chat
    await chat.send_chat_action(ChatAction.TYPING)

    voice_file = await msg.voice.get_file()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    audio_path = settings.uploads_dir / f"voice_{msg.message_id}.ogg"
    await voice_file.download_to_drive(audio_path)

    try:
        text = await _transcribe(audio_path)
    except Exception as exc:
        logger.warning("Voice transcription failed: %s", exc)
        await safe_send(chat.send_message, "Couldn't transcribe the audio.")
        return

    if not text:
        await safe_send(chat.send_message, "Empty transcription, try again.")
        return

    await safe_send(chat.send_message, f"🎙 Transcribed: _{text}_")

    orchestrator = context.application.bot_data["orchestrator"]
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    memory.log_message(user_id=user_id_db, text=text, role="user", telegram_message_id=msg.message_id)
    response = await orchestrator.process_message(user_id=user_id_db, message=text)
    await _send_response(update, response)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not await rate_limit(update, context):
        return

    msg = update.effective_message
    if msg is None or msg.document is None:
        return

    chat = update.effective_chat
    await chat.send_chat_action(ChatAction.UPLOAD_DOCUMENT)
    doc = msg.document
    file = await doc.get_file()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / (doc.file_name or f"doc_{msg.message_id}")
    await file.download_to_drive(path)

    extracted = await asyncio.to_thread(_extract_text, path)
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]

    from src.memory.db import session_scope
    from src.memory.structured import StructuredStore

    with session_scope() as s:
        StructuredStore(s).add_file(
            user_id=user_id_db,
            filename=doc.file_name,
            file_type=doc.mime_type,
            telegram_file_id=doc.file_id,
            extracted_text=extracted[:50000] if extracted else None,
        )

    if extracted:
        try:
            memory.semantic.add(
                collection="documents",
                text=extracted[:5000],
                metadata={"user_id": user_id_db, "filename": doc.file_name or "unknown"},
            )
        except Exception as exc:
            logger.warning("Document semantic indexing failed: %s", exc)

    summary = (
        extracted[:600] + ("..." if extracted and len(extracted) > 600 else "")
        if extracted else "(no text extracted)"
    )
    await safe_send(chat.send_message, f"📄 Stored **{doc.file_name}**.\n\n{summary}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not await rate_limit(update, context):
        return

    msg = update.effective_message
    if msg is None or not msg.photo:
        return

    chat = update.effective_chat
    await chat.send_chat_action(ChatAction.UPLOAD_PHOTO)

    photo = msg.photo[-1]  # highest resolution
    file = await photo.get_file()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / f"photo_{msg.message_id}.jpg"
    await file.download_to_drive(path)

    description = ""
    try:
        llm = context.application.bot_data["llm"]
        description = await llm.describe_image(path, prompt=msg.caption or "Describe this image briefly.")
    except Exception as exc:
        logger.warning("Image description failed: %s", exc)

    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]

    from src.memory.db import session_scope
    from src.memory.structured import StructuredStore

    with session_scope() as s:
        StructuredStore(s).add_file(
            user_id=user_id_db,
            filename=path.name,
            file_type="image/jpeg",
            telegram_file_id=photo.file_id,
            extracted_text=description[:50000] if description else None,
            summary=description[:500] if description else None,
        )

    if description:
        try:
            memory.semantic.add(
                collection="documents",
                text=description,
                metadata={"user_id": user_id_db, "filename": path.name, "category": "image"},
            )
        except Exception:
            pass

    body = description or "(no description generated)"
    await safe_send(chat.send_message, f"🖼 Saved photo.\n\n{body[:1500]}")


def _forwarded_origin(msg) -> Optional[str]:
    fwd = getattr(msg, "forward_origin", None)
    if fwd is None:
        return None
    name = getattr(fwd, "sender_user_name", None) or getattr(fwd, "sender_chat", None)
    if hasattr(fwd, "sender_user") and getattr(fwd.sender_user, "full_name", None):
        return fwd.sender_user.full_name
    if hasattr(fwd, "chat") and getattr(fwd.chat, "title", None):
        return fwd.chat.title
    return str(name) if name else "unknown"


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        if suffix in (".txt", ".md", ".csv", ".json", ".log"):
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".docx":
            from docx import Document

            return "\n".join(p.text for p in Document(str(path)).paragraphs)
    except Exception as exc:
        logger.warning("Text extraction failed for %s: %s", path, exc)
    return ""
