"""Proactive scanner — autonomous pings about things worth your attention.

Runs on a schedule (default every 30 min) per user. Uses the agentic loop
in 'observation mode': pulls signals (urgent unread mail, calendar events
in the next 4 hours, projects without updates in N days) and asks Sonnet
whether anything justifies a Telegram ping. If yes, the bot sends a short
proactive message — never a full dump.

Throttled per signal-type with a 6-hour cooldown so the user isn't spammed
when the same email/event keeps showing up.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from src.config import settings


logger = logging.getLogger(__name__)


# Per-user, per-signal cooldown. Key = (user_id, signal_kind, signal_id).
_LAST_PINGED: dict[tuple, float] = {}
_COOLDOWN_SECONDS = 6 * 3600


def _was_recently_pinged(key: tuple) -> bool:
    last = _LAST_PINGED.get(key, 0.0)
    return (time.time() - last) < _COOLDOWN_SECONDS


def _mark_pinged(key: tuple) -> None:
    _LAST_PINGED[key] = time.time()


SCANNER_PROMPT = """You are OMNIME's proactive scanner. You watch the user's
streams (email, calendar, projects, ideas) and decide if any single item is
worth interrupting them right now via Telegram.

Bias toward SILENCE. Most checks should produce nothing. Only ping when:
- An email needs a real reply within hours (not newsletters, not receipts).
- An event is starting within 30-60 minutes that they likely forgot.
- A project has had zero activity for >10 days and they care about it.
- An idea was captured 5-30 days ago, looks promising, and the user hasn't
  acted on it (mention which idea + suggest a small next step).
- Something time-sensitive that benefits from a heads-up.

Signals available:
{signals}

Current user profile (for tone matching):
{profile}

Output strict JSON: {{"ping": true|false, "kind": "<email|event|project|other>",
"signal_id": "<unique id of the thing being pinged about>",
"message": "<short ping in user's language, 1-3 lines max>"}}

If nothing warrants pinging, return {{"ping": false}}.
"""


async def collect_signals(memory, user_id: int) -> dict[str, Any]:
    """Pull lightweight signal data from each integration."""
    signals: dict[str, Any] = {}

    # Email: top 5 unread, focus on action class.
    try:
        from src.integrations.gmail_client import GmailClient

        gmail = GmailClient()
        if gmail.enabled:
            unread = gmail.list_unread(max_results=5)
            signals["email"] = [
                {
                    "id": m.get("id"),
                    "from": m.get("from"),
                    "subject": m.get("subject"),
                    "snippet": (m.get("snippet") or "")[:200],
                }
                for m in unread
            ]
    except Exception as exc:
        logger.debug("scanner: email signals failed: %s", exc)

    # Calendar: upcoming 4 hours.
    try:
        from src.integrations.calendar_client import CalendarClient

        cal = CalendarClient()
        if cal.enabled:
            now = datetime.utcnow()
            events = cal.list_events(now, now + timedelta(hours=4), max_results=5)
            signals["calendar"] = [
                {
                    "id": e.get("id"),
                    "summary": e.get("summary"),
                    "start": e.get("start"),
                    "location": e.get("location"),
                }
                for e in events
            ]
    except Exception as exc:
        logger.debug("scanner: calendar signals failed: %s", exc)

    # Projects with no recent activity.
    try:
        profile = memory.get_user_profile(user_id)
        active = [p for p in (profile.get("projects") or []) if p.get("status") == "active"]
        signals["projects"] = [
            {
                "name": p.get("name"),
                "last_updated": str(p.get("updated_at") or ""),
            }
            for p in active[:8]
        ]
    except Exception as exc:
        logger.debug("scanner: project signals failed: %s", exc)

    # Lingering ideas — captured 2-30 days ago that the user hasn't
    # explored further. The agent decides whether any deserves a nudge.
    try:
        from datetime import timedelta as _td
        from sqlalchemy import select as _sel

        from src.memory import models as _m
        from src.memory.db import session_scope as _scope

        with _scope() as s:
            cutoff_recent = datetime.utcnow() - _td(days=2)
            cutoff_stale = datetime.utcnow() - _td(days=30)
            rows = s.execute(
                _sel(_m.Idea)
                .where(_m.Idea.user_id == user_id)
                .where(_m.Idea.created_at <= cutoff_recent)
                .where(_m.Idea.created_at >= cutoff_stale)
                .order_by(_m.Idea.created_at.desc())
                .limit(10)
            ).scalars().all()
            signals["ideas"] = [
                {
                    "id": i.id,
                    "content": (i.content or "")[:200],
                    "captured_at": i.created_at.isoformat() if i.created_at else "",
                    "tags": i.tags or [],
                }
                for i in rows
            ]
    except Exception as exc:
        logger.debug("scanner: idea signals failed: %s", exc)

    return signals


async def proactive_scan(application, memory, llm, user_id: int) -> None:
    """One scan tick. Builds signals → asks Sonnet → maybe pings the user."""
    if not settings.proactive_enabled:
        return

    signals = await collect_signals(memory, user_id)
    if not any(signals.values()):
        return

    profile = memory.get_user_profile(user_id) or {}
    profile_block = (
        profile.get("living_profile") or profile.get("bio") or "(unknown user)"
    )[:600]

    try:
        raw = await llm.complete(
            prompt=SCANNER_PROMPT.format(
                signals=json.dumps(signals, ensure_ascii=False, default=str)[:4000],
                profile=profile_block,
            ),
            system="You output strict JSON. No commentary outside the JSON.",
            model_tier="tiny",  # Haiku — cheap; this runs every 30 min
            max_tokens=300,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("proactive_scan LLM call failed: %s", exc)
        return

    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        decision = json.loads(raw)
    except Exception:
        logger.debug("scanner: bad JSON from LLM: %r", raw[:200])
        return

    if not decision.get("ping"):
        return

    kind = (decision.get("kind") or "other").lower()
    signal_id = decision.get("signal_id") or ""
    message = (decision.get("message") or "").strip()
    if not message:
        return

    key = (user_id, kind, signal_id)
    if _was_recently_pinged(key):
        logger.debug("scanner: cooldown still active for %s, skipping", key)
        return

    chat_id = settings.proactive_chat_id or 0
    if not chat_id:
        # Fall back to the configured authorized user — populated at boot.
        chat_id = (application.bot_data or {}).get("primary_chat_id") or 0
    if not chat_id:
        logger.debug("scanner: no chat_id configured; not sending")
        return

    try:
        from src.utils.formatters import to_telegram_html
        from telegram.constants import ParseMode

        await application.bot.send_message(
            chat_id=chat_id,
            text=to_telegram_html(f"🛰 _Proactive_\n\n{message}"),
            parse_mode=ParseMode.HTML,
        )
        _mark_pinged(key)
        logger.info("scanner: pinged user about %s/%s", kind, signal_id[:30])
    except Exception as exc:
        logger.warning("scanner: send_message failed: %s", exc)
