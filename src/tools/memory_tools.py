"""Memory primitives — recall + save the user's stored knowledge.

These let the agent autonomously remember things ("lo que me dijiste sobre X
la semana pasada") and persist new facts without the user explicitly asking.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


def _memory(context: "Context"):
    # The orchestrator owns the memory manager; we look it up on the global
    # registry stash rather than passing it through every primitive.
    from src.skills.registry import get_registry

    return get_registry().memory


async def _memory_search(args: dict, context: "Context") -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    n = int(args.get("n") or 5)
    user_id = int(getattr(context, "user_id", 0) or 0)
    try:
        hits = _memory(context).semantic_search(user_id, query, n_results=n)
    except Exception as exc:
        logger.warning("memory_search failed: %s", exc)
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "query": query,
        "count": len(hits),
        "hits": [
            {
                "content": (h.content or "")[:500],
                "kind": getattr(h, "kind", None),
                "score": getattr(h, "score", None),
                "metadata": getattr(h, "metadata", {}),
            }
            for h in hits
        ],
    }, ensure_ascii=False)


MEMORY_SEARCH = Tool(
    name="memory_search",
    description=(
        "Semantic search across the user's stored knowledge — past projects, "
        "conversations, decisions, books, ideas, contacts. Use this whenever "
        "the user references their past ('lo que te conté sobre X', 'who was "
        "the recruiter from last month', 'qué decidí sobre Y') so you can "
        "ground your answer in their actual data, not invent it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "n": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    },
    run=_memory_search,
)


async def _memory_save(args: dict, context: "Context") -> str:
    content = (args.get("content") or "").strip()
    if not content:
        return json.dumps({"error": "content is required"})
    user_id = int(getattr(context, "user_id", 0) or 0)
    try:
        result = await _memory(context).process_and_store(
            user_id=user_id,
            message=content,
            context_hint=args.get("context_hint") or "",
        )
        return json.dumps({
            "stored": True,
            "summary": result.stored_summary,
            "deduplicated": result.deduplicated,
        }, ensure_ascii=False)
    except Exception as exc:
        logger.warning("memory_save failed: %s", exc)
        return json.dumps({"error": str(exc)})


MEMORY_SAVE = Tool(
    name="memory_save",
    description=(
        "Persist a new fact about the user (project, decision, contact, "
        "preference, life event…). Use when the user shares something worth "
        "remembering, or when you've learned something via web/email that "
        "should be in their profile."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Natural-language description of what to remember, in the user's voice.",
            },
            "context_hint": {
                "type": "string",
                "description": "Optional hint about where this came from (URL, email subject, etc.).",
            },
        },
        "required": ["content"],
    },
    run=_memory_save,
)


async def _memory_recall_profile(args: dict, context: "Context") -> str:
    user_id = int(getattr(context, "user_id", 0) or 0)
    try:
        profile = _memory(context).get_user_profile(user_id)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "name": profile.get("name"),
        "living_profile": profile.get("living_profile") or "",
        "communication_style": profile.get("communication_style"),
        "active_projects": [p.get("name") for p in (profile.get("projects") or []) if p.get("status") == "active"],
        "top_skills": [s.get("name") for s in (profile.get("skills") or [])[:15]],
        "recent_jobs": [
            {"role": j.get("role"), "company": j.get("company")}
            for j in (profile.get("work_experience") or [])[:5]
        ],
    }, ensure_ascii=False)


MEMORY_RECALL_PROFILE = Tool(
    name="memory_recall_profile",
    description="Get the user's full living profile (who they are, what they're working on, communication style). Use when you need to ground a response in who they are.",
    input_schema={"type": "object", "properties": {}, "required": []},
    run=_memory_recall_profile,
)


async def _memory_recent_messages(args: dict, context: "Context") -> str:
    n = int(args.get("n") or 10)
    user_id = int(getattr(context, "user_id", 0) or 0)
    try:
        msgs = _memory(context).recent_messages(user_id, limit=n)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "count": len(msgs),
        "messages": [
            {"role": m.get("role"), "text": (m.get("text") or "")[:300], "ts": str(m.get("created_at"))}
            for m in msgs
        ],
    }, ensure_ascii=False)


MEMORY_RECENT_MESSAGES = Tool(
    name="memory_recent_messages",
    description="Pull the last N conversation messages between you and the user. Use when you need to recall what was just discussed but isn't in the immediate context.",
    input_schema={
        "type": "object",
        "properties": {"n": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50}},
        "required": [],
    },
    run=_memory_recent_messages,
)


async def _memory_remind(args: dict, context: "Context") -> str:
    content = (args.get("content") or "").strip()
    when = (args.get("when") or "").strip()
    if not content or not when:
        return json.dumps({"error": "content and when are required"})
    user_id = int(getattr(context, "user_id", 0) or 0)
    due_at = _parse_when(when)
    if due_at is None:
        return json.dumps({"error": f"could not parse 'when': {when!r}. Use ISO 8601 (2026-05-13T18:00) or relative ('in 3 days', 'tomorrow 9am')."})

    from datetime import datetime as _dt
    if due_at <= _dt.utcnow():
        return json.dumps({"error": "due_at is in the past"})

    try:
        from src.memory import models as m
        from src.memory.db import session_scope

        with session_scope() as s:
            r = m.Reminder(
                user_id=user_id,
                content=content,
                context=args.get("context"),
                linked_kind=args.get("linked_kind"),
                linked_id=args.get("linked_id"),
                due_at=due_at,
            )
            s.add(r)
            s.flush()
            rid = r.id
        return json.dumps({
            "status": "scheduled",
            "id": rid,
            "due_at": due_at.isoformat(),
            "content": content,
        }, ensure_ascii=False)
    except Exception as exc:
        logger.warning("memory_remind failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _parse_when(value: str):
    """Parse 'in 3 days', 'tomorrow 9am', '2026-05-13T18:00', etc.
    Returns naive UTC datetime."""
    from datetime import datetime as _dt, timedelta
    import re as _re

    raw = value.strip().lower()

    # ISO 8601 absolute
    try:
        return _dt.fromisoformat(value.replace("Z", ""))
    except Exception:
        pass

    now = _dt.utcnow()

    # "in 3 days", "in 2 hours", "en 5 minutos"
    m = _re.match(
        r"^(?:in|en)\s+(\d+)\s+(minute|minuto|hour|hora|day|d[ií]a|week|semana|month|mes)s?",
        raw,
    )
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith(("min",)):
            return now + timedelta(minutes=n)
        if unit.startswith(("hour", "hora")):
            return now + timedelta(hours=n)
        if unit.startswith(("day", "dí", "di")):
            return now + timedelta(days=n)
        if unit.startswith(("week", "semana")):
            return now + timedelta(weeks=n)
        if unit.startswith(("month", "mes")):
            return now + timedelta(days=30 * n)

    # "tomorrow [HH:MM]" / "mañana [HH:MM]"
    m = _re.match(r"^(tomorrow|ma[ñn]ana)(?:\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?(?:\s*(am|pm))?)?", raw)
    if m:
        base = (now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
        if m.group(2):
            hh = int(m.group(2))
            mm = int(m.group(3) or 0)
            if (m.group(4) or "").lower() == "pm" and hh < 12:
                hh += 12
            base = base.replace(hour=hh, minute=mm)
        else:
            base = base.replace(hour=9)
        return base

    return None


MEMORY_REMIND = Tool(
    name="memory_remind",
    description=(
        "Schedule a future reminder. Use whenever the user says 'remind me "
        "about X', 'recuérdame Y mañana', or implicitly when they share an "
        "idea/decision that benefits from a follow-up nudge later. The bot "
        "will Telegram-ping them at due_at with the content."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "What to remind the user about (in their language)."},
            "when": {
                "type": "string",
                "description": "When to fire. Accepts ISO 8601 ('2026-05-13T18:00') or relative ('in 3 days', 'tomorrow 9am', 'in 2 hours', 'mañana', 'en 5 minutos').",
            },
            "context": {"type": "string", "description": "Optional: what was being discussed when the reminder was set."},
            "linked_kind": {"type": "string", "description": "Optional: 'idea' | 'project' | 'decision' | 'contact' if this reminder follows up on a saved entity."},
            "linked_id": {"type": "integer", "description": "Optional: id of the linked entity."},
        },
        "required": ["content", "when"],
    },
    run=_memory_remind,
)


def build_memory_tools() -> list[Tool]:
    return [MEMORY_SEARCH, MEMORY_SAVE, MEMORY_RECALL_PROFILE, MEMORY_RECENT_MESSAGES, MEMORY_REMIND]
