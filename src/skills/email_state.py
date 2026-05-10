"""In-memory per-user state shared across email skills.

When the user does ``/inbox`` and then says "responde al de Anthropic", the
composer needs to know which message that refers to. We cache the latest
inbox listing here, plus the last opened email, with a short TTL so stale
references don't linger forever.

Single-process bot → in-memory dict is fine. If we ever go multi-process
this should move to Redis or the DB.

Scheduled-send: when the user confirms a draft, we don't fire ``gmail.send()``
immediately. We register a delayed asyncio task (default 10 minutes) so the
user has a window to cancel from Telegram. Cancellations cancel the task.
If the bot restarts before the deadline, the send is silently dropped — a
safety win, since the user can re-review and resend.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


logger = logging.getLogger(__name__)


@dataclass
class InboxItem:
    id: str
    thread_id: str
    sender: str
    subject: str
    snippet: str
    date: str
    category: str = "other"  # action | personal | newsletter | other


@dataclass
class _UserEmailState:
    inbox: list[InboxItem] = field(default_factory=list)
    inbox_at: float = 0.0
    last_opened_id: Optional[str] = None
    last_opened_body: Optional[str] = None
    pending_draft: Optional[dict[str, Any]] = None  # {to, subject, body, in_reply_to, thread_id}
    pending_at: float = 0.0


_STATE: dict[int, _UserEmailState] = {}
_TTL = 30 * 60  # 30 minutes


def _now() -> float:
    return time.time()


def _state_for(user_id: int) -> _UserEmailState:
    s = _STATE.get(user_id)
    if s is None:
        s = _UserEmailState()
        _STATE[user_id] = s
    return s


def remember_inbox(user_id: int, items: list[InboxItem]) -> None:
    s = _state_for(user_id)
    s.inbox = items
    s.inbox_at = _now()


def get_inbox(user_id: int) -> list[InboxItem]:
    s = _state_for(user_id)
    if _now() - s.inbox_at > _TTL:
        s.inbox = []
    return s.inbox


def find_message(user_id: int, hint: str) -> Optional[InboxItem]:
    """Heuristic resolver: matches by ordinal ('#3', 'tercero'), by sender
    name fragment ('anthropic', 'renfe'), or by subject fragment.
    Returns the best match or None.
    """
    items = get_inbox(user_id)
    if not items:
        return None
    if not hint:
        return items[0]
    h = hint.lower().strip()

    import re

    # ordinal: "#3", "el 3", "third", "tercero"
    m = re.search(r"#?\s*(\d+)\b", h)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(items):
            return items[idx]

    ordinals_es = ["primer", "segundo", "tercer", "cuarto", "quinto", "sexto", "séptimo", "octavo", "noveno", "décimo"]
    ordinals_en = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
    for i, w in enumerate(ordinals_es + ordinals_en):
        if w in h:
            idx = i % 10
            if 0 <= idx < len(items):
                return items[idx]

    best: tuple[int, Optional[InboxItem]] = (0, None)
    for it in items:
        sender = (it.sender or "").lower()
        subj = (it.subject or "").lower()
        score = 0
        for token in re.findall(r"\w{3,}", h):
            if token in sender:
                score += 3
            if token in subj:
                score += 2
        if score > best[0]:
            best = (score, it)
    return best[1]


def remember_opened(user_id: int, message_id: str, body: str) -> None:
    s = _state_for(user_id)
    s.last_opened_id = message_id
    s.last_opened_body = body


def get_last_opened(user_id: int) -> tuple[Optional[str], Optional[str]]:
    s = _state_for(user_id)
    return s.last_opened_id, s.last_opened_body


def stash_draft(user_id: int, draft: dict[str, Any]) -> None:
    s = _state_for(user_id)
    s.pending_draft = draft
    s.pending_at = _now()


def pop_draft(user_id: int) -> Optional[dict[str, Any]]:
    s = _state_for(user_id)
    if _now() - s.pending_at > _TTL:
        s.pending_draft = None
    d = s.pending_draft
    s.pending_draft = None
    return d


def peek_draft(user_id: int) -> Optional[dict[str, Any]]:
    s = _state_for(user_id)
    if _now() - s.pending_at > _TTL:
        s.pending_draft = None
    return s.pending_draft


def render_inbox_index(items: list[InboxItem]) -> str:
    """For prompt injection: a numbered block the LLM can index into."""
    if not items:
        return "(no recent inbox listing)"
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it.sender} — {it.subject}")
    return "\n".join(lines)


# --- Scheduled sends -------------------------------------------------------

@dataclass
class ScheduledSend:
    id: str
    user_id: int
    draft: dict[str, Any]
    deadline: float
    task: asyncio.Task
    notify: Optional[Callable[[str], Awaitable[None]]] = None


_SCHEDULED: dict[str, ScheduledSend] = {}


def schedule_send(
    *,
    user_id: int,
    draft: dict[str, Any],
    delay_seconds: float,
    on_fire: Callable[[dict[str, Any]], Awaitable[Any]],
    on_complete: Optional[Callable[[str, Any, Optional[Exception]], Awaitable[None]]] = None,
) -> ScheduledSend:
    """Register a delayed send. ``on_fire(draft)`` is awaited at the deadline
    (does the actual ``gmail.send()``). ``on_complete(send_id, result, err)``
    is awaited after the send for telemetry/notification."""
    send_id = uuid.uuid4().hex[:10]

    async def _runner() -> None:
        try:
            await asyncio.sleep(max(0.0, delay_seconds))
        except asyncio.CancelledError:
            logger.info("scheduled send %s cancelled", send_id)
            raise
        result: Any = None
        err: Optional[Exception] = None
        try:
            result = await on_fire(draft)
        except Exception as exc:
            err = exc
            logger.exception("scheduled send %s failed", send_id)
        finally:
            _SCHEDULED.pop(send_id, None)
            if on_complete:
                try:
                    await on_complete(send_id, result, err)
                except Exception:
                    logger.exception("on_complete callback raised for %s", send_id)

    task = asyncio.create_task(_runner())
    rec = ScheduledSend(
        id=send_id,
        user_id=user_id,
        draft=draft,
        deadline=_now() + delay_seconds,
        task=task,
    )
    _SCHEDULED[send_id] = rec
    return rec


def cancel_scheduled(send_id: str) -> bool:
    rec = _SCHEDULED.pop(send_id, None)
    if rec is None:
        return False
    rec.task.cancel()
    return True


def get_scheduled(send_id: str) -> Optional[ScheduledSend]:
    return _SCHEDULED.get(send_id)


def list_scheduled(user_id: int) -> list[ScheduledSend]:
    return [r for r in _SCHEDULED.values() if r.user_id == user_id]
