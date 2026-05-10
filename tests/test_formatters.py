"""Telegram-flavoured HTML rendering + chunking helpers."""
from __future__ import annotations

import pytest

from src.utils.formatters import (
    chunk,
    escape_markdown,
    fmt_date,
    join_nonempty,
    safe_markdown,
    strip_markdown,
    to_telegram_html,
    truncate,
)


def test_to_telegram_html_renders_bold_italic_code():
    out = to_telegram_html("Hello **world** and _friends_ with `code`.")
    assert "<b>world</b>" in out
    assert "<i>friends</i>" in out
    assert "<code>code</code>" in out
    assert "Hello" in out


def test_to_telegram_html_handles_link():
    out = to_telegram_html("see [docs](https://example.com)")
    assert '<a href="https://example.com">docs</a>' in out


def test_to_telegram_html_escapes_html_specials():
    # Outside of markdown markers, < > & must be HTML-escaped.
    out = to_telegram_html("a < b & c > d")
    assert "&lt;" in out and "&gt;" in out and "&amp;" in out


def test_to_telegram_html_renders_fenced_code():
    out = to_telegram_html("```\nfoo()\n```")
    assert "<pre>" in out and "foo()" in out


def test_strip_markdown_removes_markers():
    assert "**" not in strip_markdown("Hello **world** and _x_.")
    assert "_" not in strip_markdown("Hello **world** and _x_.")


def test_safe_markdown_alias_returns_html():
    # Backwards-compat shim: safe_markdown now produces HTML output.
    out = safe_markdown("**bold**")
    assert "<b>bold</b>" in out


def test_escape_markdown_legacy_still_escapes_specials():
    assert escape_markdown("hello_world.") == r"hello\_world\."


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
