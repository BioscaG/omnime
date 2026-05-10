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


# In-memory upload session tracker. When the user uploads multiple files
# within UPLOAD_SESSION_GAP seconds of each other, they all land in the
# same session folder under data/uploads/<session>/. After the gap, a new
# session folder is created.
_UPLOAD_SESSIONS: dict[int, tuple[Path, float]] = {}
UPLOAD_SESSION_GAP = 5 * 60  # 5 minutes

# Burst buffer for consolidated receipts. When uploads arrive in quick
# succession, we suppress per-file acks and send a single consolidated
# message after BATCH_DEBOUNCE_SECONDS of silence.
_BATCH_BUFFER: dict[int, dict] = {}
BATCH_DEBOUNCE_SECONDS = 6.0


def _enqueue_batch_entry(user_id: int, chat, entry: dict) -> None:
    """Append one upload to the user's pending burst. Schedules (or
    reschedules) the debounce task that flushes a consolidated receipt
    when the user stops uploading for BATCH_DEBOUNCE_SECONDS. If the
    upload carried a caption (user's intent typed alongside the file),
    accumulates it for the post-flush dispatch."""
    buf = _BATCH_BUFFER.setdefault(
        user_id,
        {"chat": chat, "entries": [], "task": None, "captions": [], "context": None},
    )
    buf["chat"] = chat
    buf["entries"].append(entry)
    if entry.get("caption"):
        buf["captions"].append(entry["caption"])
    if entry.get("context"):
        buf["context"] = entry["context"]
    existing = buf.get("task")
    if existing and not existing.done():
        existing.cancel()
    buf["task"] = asyncio.create_task(_flush_batch_after_delay(user_id))


async def _flush_batch_after_delay(user_id: int) -> None:
    try:
        await asyncio.sleep(BATCH_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return
    buf = _BATCH_BUFFER.pop(user_id, None)
    if buf is None:
        return
    entries = buf["entries"]
    chat = buf["chat"]
    if not entries:
        return

    # Receipt is always neutral — the agent decides what to do based on
    # the user's next message. We don't pre-classify, don't run
    # DocumentAnalyzer, don't run the entity extractor. The user might
    # have uploaded their CV (extract personal info), a contract (extract
    # clauses), a project (analyse as a whole), or just files to store.
    # That's a Sonnet decision, not a hardcoded rule.
    sessions = sorted({e.get("session") for e in entries if e.get("session")})
    session_label = sessions[0] if len(sessions) == 1 else "(multiple)"
    file_lines = [f"  · `{e.get('filename')}`" for e in entries[:20]]
    if len(entries) > 20:
        file_lines.append(f"  · _(+{len(entries) - 20} more)_")
    if len(entries) == 1:
        e = entries[0]
        chunk_count = e.get("chunk_count", 0)
        receipt = (
            f"📎 **{e.get('filename')}** stored in `{session_label}/` "
            f"({chunk_count} chunk(s) indexed)."
        )
    else:
        receipt = (
            f"📦 **{len(entries)} files** stored in `{session_label}/`\n\n"
            + "\n".join(file_lines)
        )
    try:
        await safe_send(chat.send_message, receipt)
    except Exception as exc:
        logger.warning("batch flush send failed: %s", exc)
    logger.info(
        "batch_flush: user=%d files=%d session=%s",
        user_id, len(entries), session_label,
    )

    # If the user attached a caption to any of the uploads (their intent —
    # 'analiza esto', 'es mi cv guarda info'), dispatch it to the agentic
    # loop NOW so the agent acts on the freshly uploaded batch in context.
    captions = [c for c in (buf.get("captions") or []) if c.strip()]
    ctx_pkg = buf.get("context")
    if captions and ctx_pkg:
        combined_intent = "\n".join(captions)
        # Annotate with which files were just uploaded so Sonnet has it
        # explicit in the conversation.
        annotated = (
            f"[user just uploaded {len(entries)} file(s) into "
            f"`{session_label}/`: "
            f"{', '.join(e.get('filename') for e in entries[:20])}]\n\n"
            f"User: {combined_intent}"
        )
        try:
            orchestrator = ctx_pkg["orchestrator"]
            memory = ctx_pkg["memory"]
            user_id_db = ctx_pkg["user_id_db"]
            memory.log_message(user_id=user_id_db, text=combined_intent, role="user")
            response = await orchestrator.process_message(
                user_id=user_id_db, message=annotated,
            )
            if response.text:
                await safe_send(chat.send_message, response.text[:4000])
        except Exception as exc:
            logger.exception("post-flush dispatch failed: %s", exc)


def _slugify_for_dir(text: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:30] or "upload"


def _get_or_create_session_dir(user_id: int, hint_filename: str = "") -> Path:
    """Group uploads from the same user within UPLOAD_SESSION_GAP into
    a single subfolder of data/uploads/. Folder name: YYYY-MM-DD_HH-MM_<slug>."""
    import time as _time

    now = _time.time()
    cached = _UPLOAD_SESSIONS.get(user_id)
    if cached:
        path, last_at = cached
        if now - last_at < UPLOAD_SESSION_GAP and path.exists():
            _UPLOAD_SESSIONS[user_id] = (path, now)
            return path

    from datetime import datetime as _dt

    stamp = _dt.utcnow().strftime("%Y-%m-%d_%H-%M")
    slug = _slugify_for_dir(Path(hint_filename).stem)
    folder_name = f"{stamp}_{slug}" if slug else stamp
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / folder_name
    i = 2
    while path.exists():
        path = settings.uploads_dir / f"{folder_name}_{i}"
        i += 1
    path.mkdir(parents=True, exist_ok=True)
    _UPLOAD_SESSIONS[user_id] = (path, now)
    return path


async def _get_whisper_model():
    """Lazy-load faster-whisper. Model downloaded on first use (~150MB,
    cached under ~/.cache/huggingface). Runs on CPU; ~2-5s for a 30s
    voice note on a modest VPS."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    async with _whisper_lock:
        if _whisper_model is None:
            def _load():
                from faster_whisper import WhisperModel

                # 'base' is the sweet spot for CPU + Spanish/English voice.
                return WhisperModel("base", device="cpu", compute_type="int8")
            _whisper_model = await asyncio.to_thread(_load)
    return _whisper_model


async def _transcribe(path: Path) -> str:
    """Local-only transcription via faster-whisper. Free, no API call."""
    model = await _get_whisper_model()

    def _run() -> str:
        segments, _info = model.transcribe(str(path), beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()

    return await asyncio.to_thread(_run)


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

    # Telegram's API occasionally times out fetching a file in a burst;
    # retry a couple of times before giving up so a single network blip
    # doesn't drop one chapter of the user's TFG upload.
    from telegram.error import TimedOut, NetworkError

    last_exc: Exception | None = None
    file = None
    for attempt in range(3):
        try:
            file = await doc.get_file(read_timeout=60.0, connect_timeout=20.0)
            break
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            logger.warning(
                "doc.get_file timed out (attempt %d/3) for %s: %s",
                attempt + 1, doc.file_name, exc,
            )
            await asyncio.sleep(2 + attempt * 2)
    if file is None:
        await safe_send(
            chat.send_message,
            f"⚠️ Couldn't fetch _{doc.file_name}_ from Telegram (network "
            f"timeout). Please re-send that file.",
        )
        return

    session_dir = _get_or_create_session_dir(user_id_db, doc.file_name or "")
    raw_name = doc.file_name or f"doc_{msg.message_id}"
    path = session_dir / raw_name
    j = 2
    while path.exists():
        stem = Path(raw_name).stem
        ext = Path(raw_name).suffix
        path = session_dir / f"{stem}_{j}{ext}"
        j += 1
    await file.download_to_drive(path)
    # Compute the path relative to data/uploads/ — this is what we store in
    # FileRecord.filename so the rest of the codebase resolves it correctly.
    rel_name = str(path.relative_to(settings.uploads_dir))

    # Zip / archive uploads → extract to a subdirectory and register as a
    # 'folder' FileRecord. The agent then sends the whole tree to
    # claude_code_analyze when the user asks for analysis.
    if (doc.file_name or "").lower().endswith((".zip", ".tar", ".tar.gz", ".tgz")):
        import shutil as _shutil

        folder_name = path.stem  # e.g. "tfg" from "tfg.zip"
        folder_path = session_dir / folder_name
        i = 2
        while folder_path.exists():
            folder_path = session_dir / f"{folder_name}_{i}"
            i += 1
        try:
            _shutil.unpack_archive(str(path), extract_dir=str(folder_path))
        except Exception as exc:
            await safe_send(chat.send_message, f"⚠️ Couldn't unpack {doc.file_name}: {exc}")
            return
        # Build a tree summary for the FileRecord.
        entries = []
        total_files = 0
        total_size = 0
        for child in folder_path.rglob("*"):
            if child.is_file():
                total_files += 1
                total_size += child.stat().st_size
                if len(entries) < 100:
                    entries.append(str(child.relative_to(folder_path)))
        summary = (
            f"Archive '{doc.file_name}' extracted to folder `{folder_name}/`. "
            f"{total_files} files, {total_size // 1024} KB total. "
            f"First entries: {', '.join(entries[:10])}"
        )
        rel_folder = str(folder_path.relative_to(settings.uploads_dir))
        with session_scope() as s:
            from src.memory.structured import StructuredStore
            stored = StructuredStore(s).add_file(
                user_id=user_id_db,
                filename=rel_folder,
                file_type="folder",
                telegram_file_id=doc.file_id,
                extracted_text=None,
                summary=summary,
                tags=["archive", "folder"],
                extra_metadata={
                    "category": "folder",
                    "title": folder_name,
                    "original_archive": doc.file_name,
                    "file_count": total_files,
                    "total_size_bytes": total_size,
                    "entries_preview": entries[:50],
                },
            )
            stored_id = stored.id
        # Keep the original archive on disk too, for reference.
        await safe_send(
            chat.send_message,
            f"📦 Extracted **{doc.file_name}** → folder `{folder_name}/` "
            f"(id #{stored_id}, {total_files} files, {total_size // 1024} KB).\n\n"
            f"Ask me anything about it — for substantial analysis I'll use Claude Code on the whole tree.",
        )
        return

    extracted = await asyncio.to_thread(_extract_text, path)

    # Cheap per-file work: store basic FileRecord + chunk + index. The
    # heavy work (DocumentAnalyzer, entity extractor, rich receipt) is
    # deferred to the batch flush so a burst of uploads doesn't fire N
    # parallel Sonnet calls — and so we can decide at flush time whether
    # to treat the batch as ONE project (2+ files) or analyse the single
    # standalone file.
    from src.skills.document_analyzer import chunk_text

    from src.memory.db import session_scope
    from src.memory.structured import StructuredStore

    with session_scope() as s:
        stored = StructuredStore(s).add_file(
            user_id=user_id_db,
            filename=rel_name,
            file_type=doc.mime_type,
            telegram_file_id=doc.file_id,
            extracted_text=(extracted or "")[:50000] if extracted else None,
            summary=None,
            tags=None,
            extra_metadata={
                "category": "pending",
                "title": doc.file_name,
                "session": rel_name.split("/")[0] if "/" in rel_name else None,
            },
        )
        stored_id = stored.id

    chunk_count = 0
    if extracted:
        chunks = chunk_text(extracted, target_chars=1800, overlap=200)
        chunk_count = len(chunks)
        for i, chunk_text_value in enumerate(chunks):
            try:
                memory.semantic.add(
                    collection="documents",
                    text=chunk_text_value,
                    metadata={
                        "user_id": user_id_db,
                        "filename": rel_name,
                        "chunk": i,
                        "of": chunk_count,
                    },
                )
            except Exception as exc:
                logger.warning("Document chunk %d indexing failed: %s", i, exc)

    # Capture caption if the user typed text alongside the file. This is
    # how 'Tienes aqui mi TFG. Analízalo' arrives — as a caption on one
    # of the uploads, NOT as a separate text message.
    caption = (msg.caption or "").strip()

    # Buffer for the flush: store metadata + dispatch refs so the post-
    # flush trigger can hand the user's intent to the orchestrator.
    _enqueue_batch_entry(
        user_id_db,
        chat,
        {
            "file_id": stored_id,
            "filename": doc.file_name,
            "rel_name": rel_name,
            "session": rel_name.split("/")[0] if "/" in rel_name else None,
            "extracted": extracted or "",
            "mime_type": doc.mime_type,
            "chunk_count": chunk_count,
            "caption": caption,
            "context": {
                "orchestrator": context.application.bot_data["orchestrator"],
                "memory": memory,
                "user_id_db": user_id_db,
            },
        },
    )


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
    user_id_db = context.application.bot_data["user_id_db"]
    session_dir = _get_or_create_session_dir(user_id_db, "photo")
    path = session_dir / f"photo_{msg.message_id}.jpg"
    await file.download_to_drive(path)
    rel_name = str(path.relative_to(settings.uploads_dir))

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
            filename=rel_name,
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
                metadata={"user_id": user_id_db, "filename": rel_name, "category": category},
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


# Suffixes we KNOW are text (single read_text call). Anything else falls
# through to the binary-or-text sniffer below.
TEXT_SUFFIXES = {
    # Plain text / markup
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".properties",
    # LaTeX / bibliography / scientific writing
    ".tex", ".bib", ".cls", ".sty", ".bst", ".bbl",
    # Code (most common languages)
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".scala",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".rb", ".php", ".pl", ".swift", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".html", ".htm", ".xml", ".css", ".scss", ".sass", ".less",
    ".sql", ".graphql", ".gql", ".proto",
    ".vim", ".lua", ".r", ".jl",
    ".dockerfile",
}


def _looks_like_text(path: Path, max_bytes: int = 4096) -> bool:
    """Sniff the first few KB to decide if a file is text. NUL bytes or a
    high ratio of non-printables → binary. Used as a fallback when the
    suffix isn't on our known list."""
    try:
        sample = path.read_bytes()[:max_bytes]
    except Exception:
        return False
    if b"\x00" in sample:
        return False
    if not sample:
        return False
    # Decode-friendly?
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            sample.decode("latin-1")
            # latin-1 always succeeds, so additionally require a high
            # ratio of printable bytes.
            printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
            return printable / max(1, len(sample)) > 0.85
        except Exception:
            return False


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        if suffix == ".docx":
            from docx import Document

            return "\n".join(p.text for p in Document(str(path)).paragraphs)
        if suffix in TEXT_SUFFIXES:
            return path.read_text(encoding="utf-8", errors="ignore")
        # Fallback: sniff. Lots of code/config/data files have unusual
        # extensions but are plain text — read them anyway.
        if _looks_like_text(path):
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        logger.warning("Text extraction failed for %s: %s", path, exc)
    return ""
