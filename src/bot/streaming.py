"""Stream LLM output to Telegram by editing a single message in place.

Telegram rate-limits message edits at roughly 1 edit/sec; we debounce
~1.5s with a hard minimum between edits and a final flush at the end.
Falls back to plain-text if MarkdownV2 rendering fails on a partial chunk.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Optional

from telegram import Message
from telegram.constants import ParseMode

from src.utils.formatters import to_telegram_html, strip_markdown


logger = logging.getLogger(__name__)


class TelegramStreamer:
    def __init__(
        self,
        message: Message,
        debounce_seconds: float = 1.5,
        max_chars: int = 3500,
    ) -> None:
        self._anchor: Message = message
        self._debounce = debounce_seconds
        self._max_chars = max_chars
        self._buffer = ""
        self._messages: list[Message] = [message]
        self._last_edit = 0.0
        self._dirty = False
        self._lock = asyncio.Lock()

    async def push(self, chunk: str) -> None:
        if not chunk:
            return
        self._buffer += chunk
        self._dirty = True
        now = time.monotonic()
        if now - self._last_edit >= self._debounce:
            await self._flush(force=False)

    async def finalize(self) -> str:
        await self._flush(force=True)
        return self._buffer

    async def _flush(self, force: bool) -> None:
        async with self._lock:
            if not self._dirty:
                return
            tail = self._buffer
            # Telegram caps each message; if we exceed the budget, send a new
            # message and start a fresh anchor.
            while len(tail) > self._max_chars:
                head = tail[: self._max_chars]
                tail = tail[self._max_chars:]
                await self._safe_edit(self._anchor, head)
                new_msg = await self._anchor.reply_text("…")
                self._anchor = new_msg
                self._messages.append(new_msg)
                self._buffer = tail
            if tail or force:
                await self._safe_edit(self._anchor, tail)
            self._last_edit = time.monotonic()
            self._dirty = False

    async def _safe_edit(self, msg: Message, text: str) -> None:
        if not text:
            return
        try:
            await msg.edit_text(to_telegram_html(text), parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.debug("HTML edit failed (%s); retrying stripped plain", exc)
            try:
                await msg.edit_text(strip_markdown(text))
            except Exception as exc2:
                # Frequent: "message is not modified" when the buffer didn't grow.
                if "not modified" not in str(exc2).lower():
                    logger.warning("Plain edit also failed: %s", exc2)


async def stream_to_message(
    message: Message,
    chunks: AsyncIterator[str],
    debounce_seconds: float = 1.5,
) -> str:
    streamer = TelegramStreamer(message, debounce_seconds=debounce_seconds)
    async for chunk in chunks:
        await streamer.push(chunk)
    return await streamer.finalize()
