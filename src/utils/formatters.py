"""Text and Markdown formatting helpers used by the bot.

We render LLM output (which uses GitHub-flavoured Markdown — ``**bold**``,
``*italic*``, backticks, links) as Telegram **HTML**. HTML is far more robust
than MarkdownV2 because it only requires escaping three characters
(``<``, ``>``, ``&``) instead of the 18+ specials MarkdownV2 demands. We
fall back to plain text on any failure so messages never silently get
dropped.
"""
from __future__ import annotations

import html as _html
import logging
import re
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from telegram.constants import ParseMode


logger = logging.getLogger(__name__)


# Token regex matches: **bold**, *italic*, __underline__, _italic_, `code`,
# ```fenced```, and [label](url). Order matters — longer tokens first.
_TOKEN_RE = re.compile(
    r"```([^\n`]*\n)?(.*?)```"            # 1-2 fenced code (greedy is dangerous; we use re.DOTALL with non-greedy ?)
    r"|`([^`\n]+)`"                         # 3   inline code
    r"|\*\*\*([^*\n]+)\*\*\*"              # 4   bold-italic
    r"|\*\*([^*\n]+)\*\*"                  # 5   bold
    r"|__([^_\n]+)__"                       # 6   underline (Telegram quirk: __ = underline; we render as bold)
    r"|\*([^*\n]+)\*"                       # 7   italic
    r"|(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])"  # 8   italic via _
    r"|\[([^\]]+)\]\(([^)\s]+)\)"           # 9-10 link
    , re.DOTALL,
)


def _escape_html(text: str) -> str:
    return _html.escape(text or "", quote=False)


def to_telegram_html(text: str) -> str:
    """Convert GitHub-style Markdown into Telegram-flavoured HTML.

    Telegram supports: <b>, <i>, <u>, <s>, <a href>, <code>, <pre>,
    <pre><code class="language-...">. We map:
      **X**       -> <b>X</b>
      ***X***     -> <b><i>X</i></b>
      __X__       -> <b>X</b>     (visually the same as bold; Telegram's <u>
                                    is rarely useful and confuses users)
      *X* / _X_   -> <i>X</i>
      `X`         -> <code>X</code>
      ```...```   -> <pre>...</pre>  (with optional language hint)
      [t](url)    -> <a href="url">t</a>
    Anything else gets HTML-escaped so it survives Telegram's parser.
    """
    if not text:
        return ""

    out: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        # Plaintext between tokens.
        if m.start() > pos:
            out.append(_escape_html(text[pos:m.start()]))
        if m.group(2) is not None:  # fenced code
            lang = (m.group(1) or "").strip()
            code = _escape_html(m.group(2))
            if lang:
                out.append(f'<pre><code class="language-{_escape_html(lang)}">{code}</code></pre>')
            else:
                out.append(f"<pre>{code}</pre>")
        elif m.group(3) is not None:  # inline code
            out.append(f"<code>{_escape_html(m.group(3))}</code>")
        elif m.group(4) is not None:  # ***bold-italic***
            out.append(f"<b><i>{_escape_html(m.group(4))}</i></b>")
        elif m.group(5) is not None:  # **bold**
            out.append(f"<b>{_escape_html(m.group(5))}</b>")
        elif m.group(6) is not None:  # __underline__-as-bold
            out.append(f"<b>{_escape_html(m.group(6))}</b>")
        elif m.group(7) is not None:  # *italic*
            out.append(f"<i>{_escape_html(m.group(7))}</i>")
        elif m.group(8) is not None:  # _italic_
            out.append(f"<i>{_escape_html(m.group(8))}</i>")
        elif m.group(9) is not None:  # [label](url)
            label = _escape_html(m.group(9))
            url = m.group(10).replace('"', "%22")
            out.append(f'<a href="{url}">{label}</a>')
        pos = m.end()
    if pos < len(text):
        out.append(_escape_html(text[pos:]))
    return "".join(out)


def strip_markdown(text: str) -> str:
    """Best-effort plaintext: drop all markdown markers, keep content."""
    if not text:
        return ""
    text = re.sub(r"```[a-zA-Z0-9]*\n?", "", text)
    text = re.sub(r"```", "", text)
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    return text


async def safe_send(send_func: Callable[..., Awaitable[Any]], text: str, **kwargs: Any) -> Any:
    """Send via Telegram HTML mode. Any parse error → strip markdown, send plain."""
    if not text:
        return await send_func(text="", **kwargs)
    try:
        return await send_func(
            text=to_telegram_html(text),
            parse_mode=ParseMode.HTML,
            **kwargs,
        )
    except Exception as exc:
        logger.warning("HTML send failed (%s); retrying stripped plaintext", exc)
        try:
            return await send_func(text=strip_markdown(text), **kwargs)
        except Exception as exc2:
            logger.error("Plain-text send also failed: %s", exc2)
            raise


# --- Backwards-compat shims (some skills/tests still import these) ---------

_MDV2_SPECIALS = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_markdown(text: str) -> str:
    """Legacy MarkdownV2 escape — kept so old code/tests don't break."""
    return _MDV2_SPECIALS.sub(r"\\\1", text or "")


def safe_markdown(text: str) -> str:
    """Legacy entry-point used in a couple of places. Now returns Telegram HTML."""
    return to_telegram_html(text)


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
