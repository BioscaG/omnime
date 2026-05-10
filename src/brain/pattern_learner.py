"""Pattern learning — distill durable user preferences from recent activity.

Runs on a schedule (default daily). Looks at the last ~50 messages and
the last ~200 tool calls, asks Haiku to extract any signals about how
the user prefers things (response length, tone, scheduling cadence,
favourite tools), and upserts them into ``user_preferences`` so future
turns can ground in them.

Confidence rises with repeated observation, falls when contradicted.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.memory import models as m
from src.memory.db import session_scope
from src.memory.observability import upsert_preference


logger = logging.getLogger(__name__)


EXTRACT_PROMPT = """Look at the user's recent messages and tool-call history below.
Extract any DURABLE behavioural preferences worth remembering for future turns.

Examples of good preferences:
- response_style / brevity → "very short, 2-3 lines max"
- response_style / language → "always Spanish for chat, English in code"
- tool_preference / email_send_delay → "10 minutes default, prefers cancel window"
- cadence / morning_brief → "wants briefing at 8am with calendar+inbox digest"
- general / decision_style → "ships first, refactors later"

DO NOT extract:
- One-off preferences ("send THIS email now")
- Things that change weekly (current project name)
- Anything about specific people or content

Output strict JSON:
{{
  "preferences": [
    {{"kind": "response_style|cadence|tool_preference|general",
      "key": "<short canonical key>",
      "value": "<concise description>"}}
  ]
}}

Empty list is fine — silence is better than guessing.

Recent messages:
{messages}

Recent tool calls:
{tools}
"""


async def run_pattern_learner(memory, llm, user_id: int) -> dict[str, Any]:
    """One pass of preference extraction. Returns a small report."""
    cutoff = datetime.utcnow() - timedelta(days=14)
    with session_scope() as s:
        msg_rows = s.execute(
            select(m.Conversation)
            .where(m.Conversation.user_id == user_id)
            .where(m.Conversation.created_at >= cutoff)
            .order_by(m.Conversation.created_at.desc())
            .limit(50)
        ).scalars().all()
        tool_rows = s.execute(
            select(m.ToolCall)
            .where(m.ToolCall.user_id == user_id)
            .where(m.ToolCall.created_at >= cutoff)
            .order_by(m.ToolCall.created_at.desc())
            .limit(200)
        ).scalars().all()

        messages_block = "\n".join(
            f"[{r.created_at:%m-%d %H:%M}] {r.role}: {(r.text or '')[:300]}"
            for r in reversed(msg_rows)
        )
        tools_block = "\n".join(
            f"[{r.created_at:%m-%d %H:%M}] {r.tool_name}({_brief_args(r.args)}) "
            f"→ {'OK' if r.ok else 'ERR'}"
            for r in reversed(tool_rows)
        )

    if not messages_block and not tools_block:
        return {"learned": 0, "reason": "no recent activity"}

    try:
        raw = await llm.complete(
            prompt=EXTRACT_PROMPT.format(
                messages=messages_block[:6000] or "(none)",
                tools=tools_block[:4000] or "(none)",
            ),
            system="You output strict JSON. Empty lists are fine — bias toward silence.",
            model_tier="tiny",
            max_tokens=500,
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning("pattern_learner LLM call failed: %s", exc)
        return {"learned": 0, "error": str(exc)}

    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw)
    except Exception as exc:
        logger.debug("pattern_learner: bad JSON %r", raw[:200])
        return {"learned": 0, "error": f"parse: {exc}"}

    prefs = data.get("preferences") or []
    saved = 0
    for p in prefs:
        kind = (p.get("kind") or "general").strip()
        key = (p.get("key") or "").strip()
        value = (p.get("value") or "").strip()
        if not key or not value:
            continue
        try:
            upsert_preference(
                user_id=user_id, kind=kind, key=key, value=value,
                confidence_delta=0.15,
            )
            saved += 1
        except Exception as exc:
            logger.warning("upsert_preference failed for %s/%s: %s", kind, key, exc)
    logger.info("pattern_learner: saved %d/%d preferences", saved, len(prefs))
    return {"learned": saved, "candidates": len(prefs)}


def _brief_args(args: dict | None) -> str:
    if not args:
        return ""
    parts = []
    for k, v in list(args.items())[:3]:
        v_str = str(v)
        if len(v_str) > 30:
            v_str = v_str[:30] + "…"
        parts.append(f"{k}={v_str}")
    return ", ".join(parts)
