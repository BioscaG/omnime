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
    """Lazy-load local whisper; only used when OpenAI Whisper API isn't
    configured. Requires `pip install openai-whisper` (heavy: pulls torch)."""
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
    """Prefer OpenAI Whisper API when OPENAI_API_KEY is set (fast, accurate,
    cheap at ~\$0.006/min). Falls back to local whisper if the package is
    installed."""
    if settings.openai_api_key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            with path.open("rb") as fh:
                resp = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=fh,
                )
            return (resp.text or "").strip()
        except Exception as exc:
            logger.warning("OpenAI transcription failed: %s — trying local whisper", exc)

    # Fallback: local whisper. Will raise ImportError if not installed.
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

    await _send_extras(chat, response)


async def _send_extras(chat, response) -> None:
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
    user = update.effective_user
    msg_preview = (update.effective_message.text or "")[:80] if update.effective_message else ""
    logger.info("handle_text: user_id=%s text=%r", user.id if user else "?", msg_preview)

    if not await authorize(update, context):
        logger.warning("handle_text: rejected by authorize for user=%s", user.id if user else "?")
        return
    if not await rate_limit(update, context):
        logger.warning("handle_text: rate-limited user=%s", user.id if user else "?")
        return

    msg = update.effective_message
    if msg is None or msg.text is None:
        return

    chat = update.effective_chat
    await chat.send_chat_action(ChatAction.TYPING)
    logger.info("handle_text: dispatching to orchestrator")

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

    placeholder = None
    if settings.enable_streaming:
        # Send a placeholder so the orchestrator can stream into it.
        placeholder = await chat.send_message("…")

    response = await orchestrator.process_message(
        user_id=user_id_db, message=incoming, stream_message=placeholder,
    )

    streamed_into_placeholder = (
        placeholder is not None
        and response.intent
        and response.intent.value in ("CHAT", "QUERY")
    )
    if streamed_into_placeholder:
        # Streamer already wrote into the placeholder; only send the extras.
        if response.inline_buttons or response.files:
            await _send_extras(chat, response)
    else:
        if placeholder is not None:
            try:
                await placeholder.delete()
            except Exception:
                pass
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

    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    llm = context.application.bot_data["llm"]

    # Dedup: Telegram returns the same file_id for identical re-uploads.
    # If we already have this file_id for this user, skip the heavy work
    # and just acknowledge.
    from src.memory.db import session_scope
    from src.memory.structured import StructuredStore

    existing_summary: str | None = None
    existing_id: int | None = None
    with session_scope() as s:
        existing = StructuredStore(s).find_file_by_telegram_id(user_id_db, doc.file_id)
        if existing is not None:
            existing_summary = existing.summary or "(saved earlier)"
            existing_id = existing.id

    if existing_id is not None:
        await safe_send(
            chat.send_message,
            f"📄 Already have this one — _{doc.file_name}_ (id #{existing_id}). "
            f"Skipping re-processing.\n\n{(existing_summary or '')[:400]}",
        )
        return

    file = await doc.get_file()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / (doc.file_name or f"doc_{msg.message_id}")
    await file.download_to_drive(path)

    extracted = await asyncio.to_thread(_extract_text, path)

    if not extracted:
        await safe_send(chat.send_message, f"📄 Stored **{doc.file_name}** (no text extracted).")
        return

    # 1. Classify + summarise the document.
    from src.skills.document_analyzer import DocumentAnalyzer, chunk_text

    analyzer = DocumentAnalyzer(llm)
    analysis = await analyzer.analyze(extracted)

    # 2. Persist the file row with rich metadata.
    from src.memory.db import session_scope
    from src.memory.structured import StructuredStore

    with session_scope() as s:
        StructuredStore(s).add_file(
            user_id=user_id_db,
            filename=doc.file_name,
            file_type=doc.mime_type,
            telegram_file_id=doc.file_id,
            extracted_text=extracted[:50000],
            summary=analysis.summary or None,
            tags=analysis.tags or None,
            extra_metadata={
                "category": analysis.category,
                "title": analysis.title,
                "language": analysis.language,
                "key_entities": analysis.key_entities,
            },
        )

    # 3. Chunk the full text and index every chunk semantically.
    chunks = chunk_text(extracted, target_chars=1800, overlap=200)
    for i, chunk_text_value in enumerate(chunks):
        try:
            memory.semantic.add(
                collection="documents",
                text=chunk_text_value,
                metadata={
                    "user_id": user_id_db,
                    "filename": doc.file_name or "unknown",
                    "category": analysis.category,
                    "chunk": i,
                    "of": len(chunks),
                },
            )
        except Exception as exc:
            logger.warning("Document chunk %d indexing failed: %s", i, exc)

    # 4. Run the entity extractor over any document with substantial text.
    # The analyzer's `should_extract_personal` flag was too conservative —
    # dropping facts from contracts, invoices, papers etc. The extractor's
    # own dedup logic handles spam-prevention; cost is bounded by the
    # 8000-char cap.
    extraction_summary = ""
    if extracted and len(extracted) > 200:
        try:
            result = await memory.process_and_store(
                user_id=user_id_db,
                message=extracted[:8000],
                context_hint=(
                    f"This text comes from an uploaded document classified "
                    f"as '{analysis.category}', titled '{analysis.title or doc.file_name}'."
                ),
            )
            extraction_summary = result.stored_summary
        except Exception as exc:
            logger.warning("Document entity extraction failed: %s", exc)

    # 5. Reply with a structured receipt.
    bullets: list[str] = []
    bullets.append(f"📄 **{analysis.title or doc.file_name}**")
    bullets.append(f"Type: `{analysis.category}` · {len(chunks)} chunk(s) indexed · saved to files")
    if analysis.tags:
        bullets.append(f"Tags: {', '.join(analysis.tags)}")
    if analysis.summary:
        bullets.append(f"\n{analysis.summary}")
    if analysis.key_entities:
        bullets.append(f"\n_Mentioned: {', '.join(analysis.key_entities[:6])}_")
    if extraction_summary and extraction_summary != "nothing new":
        bullets.append(f"\n💾 Saved to memory: {extraction_summary}")
    bullets.append("\n_Ask me anything about it later — I can search across your files._")

    await safe_send(chat.send_message, "\n".join(bullets))


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
        # Richer prompt: not just describe, but transcribe any visible text
        # AND surface key facts the user might want to act on.
        prompt = (
            (msg.caption + "\n\n") if msg.caption else ""
        ) + (
            "Describe this image. If there's text in it (screenshot, "
            "whiteboard, handwritten note, sign, receipt), TRANSCRIBE it "
            "verbatim. Then list any key facts worth remembering "
            "(dates, names, amounts, action items) as bullets. Match "
            "the user's language."
        )
        description = await llm.describe_image(path, prompt=prompt)
    except Exception as exc:
        logger.warning("Image description failed: %s", exc)

    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]

    from src.memory.db import session_scope
    from src.memory.structured import StructuredStore

    # Crude category detection from the description.
    desc_lower = (description or "").lower()
    category = "image"
    if any(k in desc_lower for k in ("receipt", "factura", "ticket", "amount", "total")):
        category = "receipt"
    elif any(k in desc_lower for k in ("screenshot", "captura de pantalla", "app interface")):
        category = "screenshot"
    elif any(k in desc_lower for k in ("whiteboard", "pizarra", "blackboard")):
        category = "whiteboard"
    elif any(k in desc_lower for k in ("handwritten", "manuscrito", "note")):
        category = "note"

    with session_scope() as s:
        StructuredStore(s).add_file(
            user_id=user_id_db,
            filename=path.name,
            file_type="image/jpeg",
            telegram_file_id=photo.file_id,
            extracted_text=description[:50000] if description else None,
            summary=description[:500] if description else None,
            extra_metadata={"category": category},
        )

    if description:
        try:
            memory.semantic.add(
                collection="documents",
                text=description,
                metadata={"user_id": user_id_db, "filename": path.name, "category": category},
            )
        except Exception:
            pass

        # If the photo carries content that might be about the user (notes,
        # screenshots of plans, whiteboard ideas), run the entity extractor
        # too — this is what makes "snap a whiteboard, get it organised"
        # actually work.
        if category in ("note", "whiteboard", "screenshot") or len(description) > 200:
            try:
                await memory.process_and_store(
                    user_id=user_id_db,
                    message=description[:8000],
                    context_hint=f"This text was extracted from an uploaded {category}.",
                )
            except Exception as exc:
                logger.debug("photo entity extraction failed: %s", exc)

    body = description or "(no description generated)"
    await safe_send(chat.send_message, f"🖼 Saved {category}.\n\n{body[:1500]}")


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
