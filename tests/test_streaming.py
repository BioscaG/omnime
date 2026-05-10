"""Telegram streamer debounces edits and finalises."""
from __future__ import annotations

import pytest

from src.bot.streaming import TelegramStreamer


class _FakeMessage:
    def __init__(self) -> None:
        self.text: str = ""
        self.edits: list[str] = []
        self.replies: list[str] = []

    async def edit_text(self, text, parse_mode=None):
        self.edits.append(text)
        self.text = text

    async def reply_text(self, text):
        self.replies.append(text)
        m = _FakeMessage()
        m.text = text
        return m


@pytest.mark.asyncio
async def test_streamer_finalize_writes_buffered_text():
    msg = _FakeMessage()
    streamer = TelegramStreamer(msg, debounce_seconds=0.0)
    await streamer.push("Hello ")
    await streamer.push("world")
    final = await streamer.finalize()
    assert final == "Hello world"
    assert msg.edits, "expected at least one edit"
    assert "Hello world" in msg.edits[-1]


@pytest.mark.asyncio
async def test_streamer_handles_long_text_by_starting_new_message():
    msg = _FakeMessage()
    streamer = TelegramStreamer(msg, debounce_seconds=0.0, max_chars=20)
    await streamer.push("a" * 50)
    final = await streamer.finalize()
    assert len(final) >= 30  # buffer cleared after the final chunk
    assert msg.replies, "should have spilled over to a new message"
