"""Tool-call audit log + user-preference storage.

Every primitive the agent invokes is recorded here with its args, latency,
success state, and a short result preview. Aggregated views power the
``/tools`` Telegram command and the pattern-learning loop.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from src.memory import models as m
from src.memory.db import session_scope


logger = logging.getLogger(__name__)


def record_tool_call(
    *,
    user_id: int,
    tool_name: str,
    args: dict | None,
    ok: bool,
    latency_ms: int | None,
    result_preview: str | None,
    turn_message: str | None = None,
    error: str | None = None,
) -> None:
    try:
        with session_scope() as s:
            s.add(m.ToolCall(
                user_id=user_id,
                tool_name=tool_name,
                args=args or {},
                ok=ok,
                latency_ms=latency_ms,
                result_preview=(result_preview or "")[:1500],
                turn_message=(turn_message or "")[:1500],
                error=(error or "")[:1500] if error else None,
            ))
    except Exception as exc:
        logger.warning("record_tool_call(%s) failed: %s", tool_name, exc)


def tool_usage_summary(user_id: int, hours: int = 24) -> dict[str, Any]:
    """Aggregate tool usage over the last N hours."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    with session_scope() as s:
        rows = s.execute(
            select(
                m.ToolCall.tool_name,
                func.count(m.ToolCall.id),
                func.avg(m.ToolCall.latency_ms),
            )
            .where(m.ToolCall.user_id == user_id)
            .where(m.ToolCall.created_at >= cutoff)
            .group_by(m.ToolCall.tool_name)
            .order_by(func.count(m.ToolCall.id).desc())
        ).all()
        total = s.execute(
            select(func.count(m.ToolCall.id)).where(
                m.ToolCall.user_id == user_id,
                m.ToolCall.created_at >= cutoff,
            )
        ).scalar() or 0
        failures = s.execute(
            select(func.count(m.ToolCall.id)).where(
                m.ToolCall.user_id == user_id,
                m.ToolCall.created_at >= cutoff,
                m.ToolCall.ok == False,  # noqa: E712
            )
        ).scalar() or 0

    breakdown = []
    for name, count, avg_ms in rows:
        breakdown.append({
            "tool": name,
            "calls": int(count),
            "avg_ms": int(avg_ms) if avg_ms else None,
        })
    return {
        "hours": hours,
        "total_calls": int(total),
        "failures": int(failures),
        "breakdown": breakdown,
    }


def recent_failures(user_id: int, n: int = 10) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(
            select(m.ToolCall)
            .where(m.ToolCall.user_id == user_id)
            .where(m.ToolCall.ok == False)  # noqa: E712
            .order_by(m.ToolCall.created_at.desc())
            .limit(n)
        ).scalars().all()
        return [
            {
                "tool": r.tool_name,
                "args": r.args,
                "error": r.error,
                "when": r.created_at.isoformat(),
            }
            for r in rows
        ]


# --- User preferences -----------------------------------------------------

def upsert_preference(
    *,
    user_id: int,
    kind: str,
    key: str,
    value: str,
    confidence_delta: float = 0.1,
) -> None:
    """Insert or refresh a user preference. Bumps evidence_count + confidence
    when the same observation reappears."""
    with session_scope() as s:
        existing = s.execute(
            select(m.UserPreference)
            .where(m.UserPreference.user_id == user_id)
            .where(m.UserPreference.kind == kind)
            .where(m.UserPreference.key == key)
        ).scalar_one_or_none()
        if existing is None:
            s.add(m.UserPreference(
                user_id=user_id, kind=kind, key=key, value=value,
                confidence=0.5, evidence_count=1,
            ))
        else:
            existing.value = value
            existing.evidence_count += 1
            existing.confidence = min(1.0, existing.confidence + confidence_delta)
            existing.last_seen_at = datetime.utcnow()


def list_preferences(user_id: int, min_confidence: float = 0.0) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(
            select(m.UserPreference)
            .where(m.UserPreference.user_id == user_id)
            .where(m.UserPreference.confidence >= min_confidence)
            .order_by(m.UserPreference.confidence.desc())
        ).scalars().all()
        return [
            {
                "kind": r.kind,
                "key": r.key,
                "value": r.value,
                "confidence": r.confidence,
                "evidence_count": r.evidence_count,
                "last_seen_at": r.last_seen_at.isoformat(),
            }
            for r in rows
        ]


def render_preferences_for_prompt(user_id: int, min_confidence: float = 0.4) -> str:
    """Format learned preferences for system-prompt injection."""
    prefs = list_preferences(user_id, min_confidence=min_confidence)
    if not prefs:
        return ""
    lines = []
    for p in prefs:
        lines.append(f"- {p['kind']}: {p['key']} → {p['value']} (conf {p['confidence']:.2f})")
    return "\n".join(lines)
