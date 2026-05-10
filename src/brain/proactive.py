"""Proactive scanner — autonomous pings about things worth your attention.

Two-stage flow:

1. **Triage (Haiku, cheap)**: pulls signals (urgent unread mail, calendar
   events in the next 4 hours, projects without updates, lingering ideas)
   and asks: "is there anything here worth investigating further?"

2. **Investigation (Sonnet + full tool catalog, only if triage says yes)**:
   runs the agentic loop with the question "investigate the signal(s)
   below, decide whether the user needs to be pinged, and if so write a
   short, useful message". The loop can call gmail_read, memory_search,
   calendar_list, web_search, etc. — same powers as a normal user turn.
   This is what makes pings *useful* instead of just alerts: the bot can
   read the actual email body, cross-reference with memory, and write a
   recommendation, not a notification.

Per-signal 6-hour cooldown stops the same item pinging twice.
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


TRIAGE_PROMPT = """You are OMNIME's proactive triage. Decide whether the
signals below warrant deeper investigation by the agent. We want to bias
TOWARD silence — most ticks should produce nothing.

Greenlight investigation when ANY of these hold:
- An email looks like it might need action (real human, not newsletter/receipt).
- An event is starting within 60 minutes that the user might benefit from
  preparing for (e.g. unread brief, no notes).
- A project has had zero activity for >10 days and they care about it.
- A captured idea (5-30 days old) looks promising and unactioned.

If you greenlight, return signal_ids the agent should focus on. If not,
return an empty list — the agent won't run.

Signals:
{signals}

Output strict JSON ONLY:
{{"investigate": true|false, "focus_signal_ids": ["..."]}}
"""


INVESTIGATION_PROMPT = """The proactive scanner just flagged the following
signal(s) as potentially worth pinging the user about. Investigate using
your tools (gmail_read for emails, calendar_list for events, memory_search
for context, web_fetch for links inside emails, etc.) and decide if the
user actually needs to know.

Greenlit signals:
{signals}

Today is {today_local}.

Rules:
- Investigate ONE signal at a time, deeply. Don't ping for everything.
- If after investigating it's not actually important → say nothing
  (write '<no ping>' as your final message and we'll skip).
- If it IS worth interrupting the user → write a short Telegram message
  (1-4 lines, in their language) that contains the actionable insight,
  not a generic alert. E.g. NOT 'tienes un email de Anthropic', but
  'Anthropic confirma la factura de mayo (€12). Ya está pagada — quizá
  archivar.'
- Be conversational, like a sharp colleague pinging you on Slack.
- DON'T schedule new reminders here — that's not your job, this is just
  a heads-up.
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
    """Two-stage scan: cheap triage → deep agentic investigation when warranted."""
    if not settings.proactive_enabled:
        return

    signals = await collect_signals(memory, user_id)
    if not any(signals.values()):
        return

    # Stage 1 — Haiku triage. Cheap.
    try:
        triage_raw = await llm.complete(
            prompt=TRIAGE_PROMPT.format(
                signals=json.dumps(signals, ensure_ascii=False, default=str)[:4000],
            ),
            system="You output strict JSON. No commentary outside the JSON.",
            model_tier="tiny",
            max_tokens=200,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("proactive triage failed: %s", exc)
        return

    triage = _safe_json(triage_raw)
    if not triage or not triage.get("investigate"):
        return

    focus_ids = [str(x) for x in (triage.get("focus_signal_ids") or [])]
    focus_signals = _filter_signals(signals, focus_ids) if focus_ids else signals
    if not any(focus_signals.values()):
        return

    # Cooldown: only investigate if at least one of the focus items hasn't
    # been pinged recently.
    cooldown_key = (user_id, "investigate", json.dumps(focus_ids, sort_keys=True))
    if _was_recently_pinged(cooldown_key):
        logger.debug("scanner: cooldown active, skipping")
        return

    # Stage 2 — invoke the agentic loop directly. The orchestrator's loop
    # already has the full primitive catalog; we just inject a synthetic
    # user message describing the investigation task.
    orchestrator = (application.bot_data or {}).get("orchestrator")
    if orchestrator is None:
        logger.warning("scanner: orchestrator not in bot_data; cannot investigate")
        return

    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Madrid"))
    except Exception:
        now_local = datetime.now(timezone.utc)

    investigation_prompt = INVESTIGATION_PROMPT.format(
        signals=json.dumps(focus_signals, ensure_ascii=False, default=str)[:4000],
        today_local=now_local.strftime("%A %d %B %Y, %H:%M"),
    )

    try:
        # Use the orchestrator's agentic loop directly. This gives the
        # scanner the full primitive catalog (gmail_read, memory_search,
        # calendar_list, web_fetch, …) and proper conversation handling
        # (tools/responses round-trip cleanly).
        from src.brain.context_builder import Context

        context = await orchestrator.context_builder.build(user_id, investigation_prompt)
        # Use a separate conversation thread for the scanner so its history
        # doesn't pollute the user's normal chat thread. Hack: bias the user
        # id to a negative space.
        scanner_user_id = -user_id
        context.user_id = scanner_user_id
        response = await orchestrator._run_agentic_loop(
            scanner_user_id, investigation_prompt, context,
        )
    except Exception as exc:
        logger.warning("scanner agentic investigation failed: %s", exc)
        return

    final_text = (response.text or "").strip()
    if not final_text or "<no ping>" in final_text.lower():
        logger.info("scanner: investigation completed, no ping warranted")
        return

    chat_id = settings.proactive_chat_id or settings.telegram_user_id or 0
    if not chat_id:
        chat_id = (application.bot_data or {}).get("primary_chat_id") or 0
    if not chat_id:
        logger.debug("scanner: no chat_id configured; not sending")
        return

    try:
        from src.utils.formatters import to_telegram_html
        from telegram.constants import ParseMode

        await application.bot.send_message(
            chat_id=chat_id,
            text=to_telegram_html(f"🛰 <b>Proactive</b>\n\n{final_text}"),
            parse_mode=ParseMode.HTML,
        )
        _mark_pinged(cooldown_key)
        logger.info("scanner: pinged user after investigation: %s", final_text[:120])
    except Exception as exc:
        logger.warning("scanner: send_message failed: %s", exc)


def _safe_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _filter_signals(signals: dict, focus_ids: list[str]) -> dict:
    """Subset signals to only items whose id matches the focus list. Falls
    through gracefully when the schema doesn't expose ids per item."""
    out: dict = {}
    for stream, items in signals.items():
        if not isinstance(items, list):
            out[stream] = items
            continue
        kept = [it for it in items if str(it.get("id") or it.get("name") or "") in focus_ids]
        out[stream] = kept or items  # fall back to full stream if no ids match
    return out
