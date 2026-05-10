"""Calendar overlap detection."""
from __future__ import annotations

from datetime import datetime

from src.integrations.calendar_client import CalendarClient


def _ev(start: str, end: str) -> dict:
    return {"start": start, "end": end, "summary": "x"}


def test_overlap_strict_inside():
    e = _ev("2026-05-10T10:00:00", "2026-05-10T11:00:00")
    assert CalendarClient._overlaps(e, datetime(2026, 5, 10, 9), datetime(2026, 5, 10, 12))


def test_no_overlap_when_adjacent():
    e = _ev("2026-05-10T11:00:00", "2026-05-10T12:00:00")
    assert not CalendarClient._overlaps(e, datetime(2026, 5, 10, 9), datetime(2026, 5, 10, 11))


def test_partial_overlap():
    e = _ev("2026-05-10T11:30:00", "2026-05-10T12:30:00")
    assert CalendarClient._overlaps(e, datetime(2026, 5, 10, 11), datetime(2026, 5, 10, 12))
