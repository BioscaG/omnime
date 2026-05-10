"""MarkdownV2 helpers and chunking."""
from __future__ import annotations

import pytest

from src.utils.formatters import (
    chunk,
    escape_markdown,
    fmt_date,
    join_nonempty,
    safe_markdown,
    truncate,
)


def test_escape_markdown_escapes_specials():
    assert escape_markdown("hello_world.") == r"hello\_world\."
    assert escape_markdown("a*b") == r"a\*b"
    assert escape_markdown("[x](y)") == r"\[x\]\(y\)"


def test_safe_markdown_preserves_bold_italic_code():
    out = safe_markdown("Hello **world** and _friends_ with `code`.")
    assert "*world*" in out
    assert "_friends_" in out
    assert "`code`" in out
    assert r"\." in out


def test_safe_markdown_handles_link():
    out = safe_markdown("see [docs](https://example.com)")
    assert "[docs](https://example.com)" in out


def test_chunk_splits_long_text():
    text = "a" * 7500
    pieces = chunk(text, size=3500)
    assert len(pieces) == 3
    assert all(len(p) <= 3500 for p in pieces)


def test_truncate_appends_suffix():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"


def test_fmt_date_handles_inputs():
    from datetime import date, datetime

    assert fmt_date(None) == "—"
    assert fmt_date(date(2026, 5, 10)) == "2026-05-10"
    assert fmt_date(datetime(2026, 5, 10, 8, 0)).startswith("2026-05-10 08:00")


def test_join_nonempty_filters_empty():
    assert join_nonempty(["a", None, "", "b"]) == "a · b"
