"""Google Calendar primitives — see, create, check availability.

Composable building blocks for the agent: 'tengo libre el martes a las 4?'
becomes calendar_check_availability; 'agéndame la entrevista que me pidió
Glovo el jueves a las 10' becomes calendar_create_event.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from src.integrations.calendar_client import CalendarClient
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


def _calendar_or_disabled() -> tuple[CalendarClient | None, str | None]:
    try:
        client = CalendarClient()
    except Exception as exc:
        return None, f"Calendar integration error: {exc}"
    if not client.enabled:
        return None, "Google Calendar isn't configured (GCAL_* env vars missing)."
    return client, None


def _parse_when(value: str) -> datetime | None:
    """Parse ISO-ish datetimes the model emits. Tolerates 'YYYY-MM-DD' (full
    day) and 'YYYY-MM-DDTHH:MM[:SS]' (with optional Z)."""
    if not value:
        return None
    raw = value.strip().replace("Z", "")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        try:
            # date-only → assume midnight
            return datetime.fromisoformat(raw + "T00:00:00")
        except Exception:
            return None


# --- list ------------------------------------------------------------------

async def _calendar_list(args: dict, context: "Context") -> str:
    client, err = _calendar_or_disabled()
    if err:
        return err
    days = int(args.get("days_ahead") or 7)
    start_str = (args.get("start") or "").strip()
    start = _parse_when(start_str) or datetime.utcnow()
    end_str = (args.get("end") or "").strip()
    end = _parse_when(end_str) or (start + timedelta(days=days))
    max_results = int(args.get("max_results") or 20)
    try:
        events = client.list_events(start, end, max_results=max_results)
    except Exception as exc:
        logger.warning("calendar_list failed: %s", exc)
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(events),
        "events": events,
    }, ensure_ascii=False, default=str)


CALENDAR_LIST = Tool(
    name="calendar_list",
    description=(
        "List Google Calendar events in a window. Default window: next 7 "
        "days from now. Use ``start`` / ``end`` (ISO 8601 datetimes) for a "
        "specific range, or ``days_ahead`` from now."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "ISO 8601 start datetime. Optional; default = now."},
            "end": {"type": "string", "description": "ISO 8601 end datetime. Optional."},
            "days_ahead": {"type": "integer", "default": 7, "minimum": 1, "maximum": 90},
            "max_results": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
        },
        "required": [],
    },
    run=_calendar_list,
)


# --- create ----------------------------------------------------------------

async def _calendar_create(args: dict, context: "Context") -> str:
    client, err = _calendar_or_disabled()
    if err:
        return err
    summary = (args.get("summary") or "").strip()
    start = _parse_when(args.get("start") or "")
    end = _parse_when(args.get("end") or "")
    if not summary or start is None or end is None:
        return json.dumps({"error": "summary, start (ISO datetime), and end (ISO datetime) are required"})
    if end <= start:
        return json.dumps({"error": "end must be after start"})
    try:
        ev = client.create_event(
            summary=summary,
            start=start,
            end=end,
            description=args.get("description") or None,
            location=args.get("location") or None,
            attendees=args.get("attendees") or None,
        )
        return json.dumps({
            "status": "created",
            "id": ev.get("id"),
            "htmlLink": ev.get("htmlLink"),
            "summary": summary,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }, ensure_ascii=False)
    except Exception as exc:
        logger.warning("calendar_create failed: %s", exc)
        return json.dumps({"error": str(exc)})


CALENDAR_CREATE = Tool(
    name="calendar_create",
    description=(
        "Create a Google Calendar event. Always confirm with the user "
        "BEFORE calling this for events with attendees (since invites get "
        "emailed). Datetimes must be ISO 8601 — convert relative phrases "
        "('mañana a las 10', 'next Tuesday at 4pm') yourself before calling. "
        "Today is {today}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title."},
            "start": {"type": "string", "description": "ISO 8601 start datetime, e.g. '2026-05-12T10:00:00'."},
            "end": {"type": "string", "description": "ISO 8601 end datetime."},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "List of attendee email addresses."},
        },
        "required": ["summary", "start", "end"],
    },
    run=_calendar_create,
)


# --- availability ----------------------------------------------------------

async def _calendar_availability(args: dict, context: "Context") -> str:
    client, err = _calendar_or_disabled()
    if err:
        return err
    start = _parse_when(args.get("start") or "")
    end = _parse_when(args.get("end") or "")
    if start is None or end is None:
        return json.dumps({"error": "start and end (ISO datetimes) required"})
    try:
        conflicts = client.conflicts(start, end)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "from": start.isoformat(),
        "to": end.isoformat(),
        "is_free": len(conflicts) == 0,
        "conflicts": conflicts,
    }, ensure_ascii=False, default=str)


CALENDAR_AVAILABILITY = Tool(
    name="calendar_check_availability",
    description=(
        "Check whether the user is free in a given window. Use BEFORE "
        "calendar_create to avoid double-booking. Returns is_free + the "
        "list of conflicts if any."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "ISO 8601 start."},
            "end": {"type": "string", "description": "ISO 8601 end."},
        },
        "required": ["start", "end"],
    },
    run=_calendar_availability,
)


def build_calendar_tools() -> list[Tool]:
    try:
        client = CalendarClient()
        if not client.enabled:
            return []
    except Exception:
        return []
    return [CALENDAR_LIST, CALENDAR_CREATE, CALENDAR_AVAILABILITY]
