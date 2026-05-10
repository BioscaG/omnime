"""Text and Markdown formatting helpers used by the bot."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from telegram.constants import ParseMode


logger = logging.getLogger(__name__)


_MDV2_SPECIALS = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_markdown(text: str) -> str:
    """Escape every MarkdownV2 special character (treats input as plain)."""
    return _MDV2_SPECIALS.sub(r"\\\1", text or "")


_INLINE_TOKEN = re.compile(
    r"(\*\*[^*\n]+\*\*|\*[^*\n]+\*|__[^_\n]+__|_[^_\n]+_|`[^`\n]+`|\[[^\]]+\]\([^\)]+\))"
)


def safe_markdown(text: str) -> str:
    """Return MarkdownV2 with `**bold**`, `*italic*`, `` `code` `` and links preserved
    while escaping everything else. Tolerates LLM output safely."""
    if not text:
        return ""
    out: list[str] = []
    pos = 0
    for m in _INLINE_TOKEN.finditer(text):
        out.append(escape_markdown(text[pos:m.start()]))
        token = m.group(0)
        if token.startswith("**") and token.endswith("**"):
            inner = token[2:-2]
            out.append("*" + escape_markdown(inner) + "*")
        elif token.startswith("*") and token.endswith("*"):
            inner = token[1:-1]
            out.append("_" + escape_markdown(inner) + "_")
        elif token.startswith("__") and token.endswith("__"):
            inner = token[2:-2]
            out.append("__" + escape_markdown(inner) + "__")
        elif token.startswith("_") and token.endswith("_"):
            inner = token[1:-1]
            out.append("_" + escape_markdown(inner) + "_")
        elif token.startswith("`") and token.endswith("`"):
            inner = token[1:-1].replace("\\", "\\\\").replace("`", "\\`")
            out.append("`" + inner + "`")
        elif token.startswith("[") and "](" in token:
            label_end = token.index("]")
            label = token[1:label_end]
            url = token[label_end + 2 : -1]
            out.append("[" + escape_markdown(label) + "](" + url.replace(")", r"\)") + ")")
        else:
            out.append(escape_markdown(token))
        pos = m.end()
    out.append(escape_markdown(text[pos:]))
    return "".join(out)


async def safe_send(send_func: Callable[..., Awaitable[Any]], text: str, **kwargs: Any) -> Any:
    """Try MarkdownV2 first; on parse error fall back to plain text."""
    try:
        return await send_func(text=safe_markdown(text), parse_mode=ParseMode.MARKDOWN_V2, **kwargs)
    except Exception as exc:
        logger.warning("MarkdownV2 send failed (%s); retrying as plain text", exc)
        try:
            return await send_func(text=text, **kwargs)
        except Exception as exc2:
            logger.error("Plain-text send also failed: %s", exc2)
            raise


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
