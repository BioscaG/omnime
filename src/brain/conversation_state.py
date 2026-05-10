"""Per-user persistent message history for the agentic loop.

This is what production assistants (Claude.ai, ChatGPT, Cursor…) do — they
keep ONE long thread of messages per user where the model sees its own
prior tool_use blocks and tool_results across turns. No regex hacks, no
'draft persistence' as a separate side channel, no context summarisation
that loses information.

When the user types "envíalo" on Tuesday, the model sees in its message
history:
    user:      "puedes escribir un mail a X?"
    assistant: <text "Claro, dame el asunto">
    user:      "diciendo Y"
    assistant: <text with draft>  ← right there
    user:      "envíalo"  ← current
…and just calls gmail_send with the args from its own prior assistant turn.

If the model claimed to have sent something, the message history shows
whether a tool_use block was actually emitted or it was just text. So
hallucinated confirmations become impossible to repeat — the model would
contradict its own visible history.

In-memory store with a sliding TTL (default 6h of inactivity) — for a
single-process bot this is fine. Multi-process would push to Redis or DB.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class _Session:
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_active: float = 0.0


class ConversationStore:
    def __init__(
        self,
        ttl_seconds: int = 6 * 3600,
        max_messages: int = 60,
    ) -> None:
        self._sessions: dict[int, _Session] = {}
        self._ttl = ttl_seconds
        self._max_messages = max_messages

    def is_fresh_session(self, user_id: int) -> bool:
        session = self._sessions.get(user_id)
        if session is None:
            return True
        return (time.time() - session.last_active) > self._ttl

    def history(self, user_id: int) -> list[dict[str, Any]]:
        if self.is_fresh_session(user_id):
            return []
        return self._sessions[user_id].messages

    def append_user(self, user_id: int, content: Any) -> None:
        session = self._touch(user_id)
        session.messages.append({"role": "user", "content": content})
        self._trim(session)

    def append_assistant(self, user_id: int, raw_content: list[Any]) -> None:
        session = self._touch(user_id)
        # Convert Anthropic block objects into plain dicts so they round-trip
        # through subsequent .messages.create() calls without choking.
        session.messages.append({
            "role": "assistant",
            "content": [_serialize_block(b) for b in raw_content],
        })
        self._trim(session)

    def append_tool_results(
        self, user_id: int, tool_results: list[dict[str, Any]]
    ) -> None:
        session = self._touch(user_id)
        session.messages.append({"role": "user", "content": tool_results})
        self._trim(session)

    def reset(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)
        logger.info("conversation reset: user=%d", user_id)

    def _touch(self, user_id: int) -> _Session:
        session = self._sessions.get(user_id)
        now = time.time()
        if session is None or (now - session.last_active) > self._ttl:
            session = _Session(messages=[], last_active=now)
            self._sessions[user_id] = session
        session.last_active = now
        return session

    def _trim(self, session: _Session) -> None:
        """Keep only the last N messages. Anthropic's context window is
        large but each turn keeps growing — bound it at something reasonable.
        Special care: if the trim would orphan a tool_use without its
        tool_result (or vice-versa), the API errors out. So we trim from
        the front and only between balanced points."""
        if len(session.messages) <= self._max_messages:
            return
        excess = len(session.messages) - self._max_messages
        # Walk from the front, skipping pairs that would orphan tool blocks.
        i = 0
        while excess > 0 and i < len(session.messages):
            msg = session.messages[i]
            if _carries_tool_use(msg):
                # Skip this and the immediately-following user-tool_result
                # so the pair stays together.
                if i + 1 < len(session.messages) and _carries_tool_result(session.messages[i + 1]):
                    # Drop the pair.
                    del session.messages[i:i + 2]
                    excess -= 2
                    continue
            del session.messages[i]
            excess -= 1


def _serialize_block(block: Any) -> dict[str, Any]:
    """Convert an Anthropic SDK content block into a JSON-serialisable dict
    we can re-send in the next ``messages`` array."""
    if isinstance(block, dict):
        return block
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "") or ""}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": dict(getattr(block, "input", {}) or {}),
        }
    if btype == "thinking":
        return {"type": "thinking", "thinking": getattr(block, "thinking", "")}
    # Fallback: best-effort dict conversion.
    return {"type": btype or "unknown", "raw": repr(block)[:500]}


def _carries_tool_use(msg: dict[str, Any]) -> bool:
    if msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    return False


def _carries_tool_result(msg: dict[str, Any]) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return False


# Module-level singleton — in-process state.
_STORE: ConversationStore | None = None


def get_conversation_store() -> ConversationStore:
    global _STORE
    if _STORE is None:
        _STORE = ConversationStore()
    return _STORE
