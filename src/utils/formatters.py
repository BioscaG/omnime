"""Text and Markdown formatting helpers used by the bot."""
from __future__ import annotations

import re
from datetime import date, datetime


_TG_MD_ESCAPE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")


def escape_markdown(text: str) -> str:
    """Escape for Telegram MarkdownV2."""
    return _TG_MD_ESCAPE.sub(r"\\\1", text)


def truncate(text: str, max_chars: int = 4000, suffix: str = "…") -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(suffix)] + suffix


def fmt_date(value: date | datetime | str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return value.strftime("%Y-%m-%d")


def join_nonempty(parts: list[str | None], sep: str = " · ") -> str:
    return sep.join(p for p in parts if p)


def chunk(text: str, size: int = 3500) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]
