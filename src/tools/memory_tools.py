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


def build_memory_tools() -> list[Tool]:
    return [MEMORY_SEARCH, MEMORY_SAVE, MEMORY_RECALL_PROFILE, MEMORY_RECENT_MESSAGES]
