"""Periodic health checks for every external integration.

Runs every 6h. For each enabled integration, performs a cheap read
operation and pings the user via Telegram if it fails. Catches token
expiry (Google's 7-day Testing-mode lifetime!), revoked permissions,
provider outages — before the user discovers them by asking the bot
to do something and getting silence.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


# Per-integration cooldown — don't spam the user about the same broken
# integration on every 6h tick.
_LAST_PINGED: dict[str, float] = {}
_PING_COOLDOWN = 24 * 3600


def _was_recently_pinged(name: str) -> bool:
    return (time.time() - _LAST_PINGED.get(name, 0.0)) < _PING_COOLDOWN


def _mark_pinged(name: str) -> None:
    _LAST_PINGED[name] = time.time()


def _check_gmail() -> tuple[bool, str | None]:
    from src.integrations.gmail_client import GmailClient

    client = GmailClient()
    if not client.enabled:
        return True, None  # not configured = not broken
    try:
        client.list_unread(max_results=1)
        return True, None
    except Exception as exc:
        return False, f"Gmail: {exc}"


def _check_calendar() -> tuple[bool, str | None]:
    from datetime import datetime, timedelta

    from src.integrations.calendar_client import CalendarClient

    client = CalendarClient()
    if not client.enabled:
        return True, None
    try:
        client.list_events(datetime.utcnow(), datetime.utcnow() + timedelta(days=1), max_results=1)
        return True, None
    except Exception as exc:
        return False, f"Calendar: {exc}"


def _check_drive() -> tuple[bool, str | None]:
    from src.integrations.drive_client import DriveClient

    client = DriveClient()
    if not client.enabled:
        return True, None
    try:
        client.list_files(max_results=1)
        return True, None
    except Exception as exc:
        return False, f"Drive: {exc}"


def _check_notion() -> tuple[bool, str | None]:
    from src.integrations.notion_client import NotionClient

    client = NotionClient()
    if not client.enabled:
        return True, None
    try:
        # Notion's search returns within a few hundred ms on a workspace
        # of any reasonable size and validates the token.
        import asyncio

        async def _go():
            try:
                await client.search("", page_size=1)
            finally:
                await client.aclose()

        asyncio.run(_go())
        return True, None
    except Exception as exc:
        return False, f"Notion: {exc}"


def _check_github() -> tuple[bool, str | None]:
    from src.integrations.github_client import GitHubClient

    client = GitHubClient()
    if not client.enabled:
        return True, None
    try:
        if client.default_repo:
            client.list_issues(state="open", max_results=1)
        else:
            client.list_repos(max_results=1)
        return True, None
    except Exception as exc:
        return False, f"GitHub: {exc}"


def _check_anthropic() -> tuple[bool, str | None]:
    """Trivial Haiku call to verify the API key is alive + has credit."""
    if not settings.anthropic_api_key:
        return True, None
    try:
        import asyncio

        from src.brain.llm_client import LLMClient

        async def _go():
            llm = LLMClient(provider="anthropic")
            await llm.complete(prompt="reply 'ok'", model_tier="tiny", max_tokens=5)

        asyncio.run(_go())
        return True, None
    except Exception as exc:
        return False, f"Anthropic: {exc}"


CHECKS: list[tuple[str, Any]] = [
    ("anthropic", _check_anthropic),
    ("gmail", _check_gmail),
    ("calendar", _check_calendar),
    ("drive", _check_drive),
    ("notion", _check_notion),
    ("github", _check_github),
]


async def run_health_check(application) -> dict[str, Any]:
    """One pass through every integration. Pings the user (with 24h
    cooldown per integration) when something fails."""
    results: dict[str, Any] = {}
    failed: list[tuple[str, str]] = []
    for name, fn in CHECKS:
        try:
            ok, msg = fn()
        except Exception as exc:
            ok, msg = False, f"{name}: check itself errored: {exc}"
        results[name] = {"ok": ok, "error": msg}
        if not ok and msg:
            failed.append((name, msg))

    if not failed:
        logger.info("health: all integrations OK")
        return results

    # Report per-integration failures once per 24h.
    new_failures = [(n, m) for n, m in failed if not _was_recently_pinged(n)]
    if not new_failures:
        return results

    chat_id = (
        settings.proactive_chat_id
        or settings.telegram_user_id
        or (application.bot_data or {}).get("primary_chat_id")
        or 0
    )
    if not chat_id:
        return results

    lines = ["🏥 <b>Health check — fallos detectados</b>\n"]
    for name, msg in new_failures:
        lines.append(f"❌ <b>{name}</b>: {msg[:200]}")
    lines.append(
        "\n<i>Probable: token caducado o revocado. Si es Google "
        "(Gmail/Drive/Calendar), corre <code>python -m scripts.setup_oauth "
        "&lt;servicio&gt;</code> y actualiza .env.</i>"
    )

    try:
        from telegram.constants import ParseMode

        await application.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
        for n, _ in new_failures:
            _mark_pinged(n)
        logger.warning("health: pinged user about failures: %s", [n for n, _ in new_failures])
    except Exception as exc:
        logger.warning("health: send_message failed: %s", exc)

    return results
