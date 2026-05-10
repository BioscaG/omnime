"""Email primitives — fine-grained Gmail operations the agent composes.

Each tool here does ONE thing and returns structured data (JSON string).
The driver model picks which to call and combines them however it wants —
e.g. for "tengo algún mail importante?" it calls ``gmail_list``, looks at
the data, filters by importance, and writes the answer in its own voice.

When Gmail isn't configured these tools return a clear error string so the
model can tell the user. They never crash the loop.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.integrations.gmail_client import GmailClient, is_noreply
from src.skills.email_state import (
    InboxItem,
    cancel_scheduled,
    pop_draft,
    remember_inbox,
    remember_opened,
    schedule_send,
    stash_draft,
)
from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context


logger = logging.getLogger(__name__)


def _gmail_or_disabled() -> tuple[GmailClient | None, str | None]:
    try:
        client = GmailClient()
    except Exception as exc:
        return None, f"Gmail integration error: {exc}"
    if not client.enabled:
        return None, "Gmail isn't configured (GMAIL_* env vars missing)."
    return client, None


def _short_sender(value: str | None) -> str:
    if not value:
        return "(unknown)"
    return value.split("<")[0].strip().strip('"') or value


# --- list -------------------------------------------------------------------

async def _gmail_list(args: dict, context: "Context") -> str:
    client, err = _gmail_or_disabled()
    if err:
        return err
    max_results = int(args.get("max_results") or 10)
    filter_kind = (args.get("filter") or "unread").lower()
    query_map = {
        "unread": "is:unread in:inbox",
        "inbox": "in:inbox",
        "today": "in:inbox newer_than:1d",
        "starred": "is:starred",
    }
    q = query_map.get(filter_kind, "is:unread in:inbox")
    try:
        msgs = client.list_unread(max_results=max_results) if filter_kind == "unread" \
            else client.search(q, max_results=max_results)
    except Exception as exc:
        logger.warning("gmail_list failed: %s", exc)
        return json.dumps({"error": str(exc)})

    user_id = int(getattr(context, "user_id", 0) or 0)
    items = [
        InboxItem(
            id=m.get("id"),
            thread_id=m.get("thread_id") or "",
            sender=m.get("from") or "",
            subject=m.get("subject") or "",
            snippet=m.get("snippet") or "",
            date=m.get("date") or "",
        )
        for m in msgs
    ]
    remember_inbox(user_id, items)

    return json.dumps({
        "count": len(items),
        "messages": [
            {
                "id": it.id,
                "from": it.sender,
                "from_short": _short_sender(it.sender),
                "subject": it.subject,
                "snippet": (it.snippet or "")[:200],
                "date": it.date,
                "is_noreply": is_noreply(it.sender),
            }
            for it in items
        ],
    }, ensure_ascii=False)


GMAIL_LIST = Tool(
    name="gmail_list",
    description=(
        "List Gmail messages with optional filter. Returns raw JSON for the "
        "model to interpret. The 10-most-recent unread is the most common "
        "case but pick a smaller max_results when you only need a quick "
        "scan. Filters: 'unread' (default), 'inbox', 'today', 'starred'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "enum": ["unread", "inbox", "today", "starred"],
                "default": "unread",
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": [],
    },
    run=_gmail_list,
)


# --- read -------------------------------------------------------------------

async def _gmail_read(args: dict, context: "Context") -> str:
    client, err = _gmail_or_disabled()
    if err:
        return err
    msg_id = (args.get("message_id") or "").strip()
    if not msg_id:
        return json.dumps({"error": "message_id is required"})
    try:
        parsed = client.get_message_parsed(msg_id)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    user_id = int(getattr(context, "user_id", 0) or 0)
    body = (parsed.get("body") or "")[:8000]
    remember_opened(user_id, msg_id, body)

    if args.get("mark_read", True):
        try:
            client.mark_read(msg_id)
        except Exception as exc:
            logger.debug("mark_read failed: %s", exc)

    return json.dumps({
        "id": parsed.get("id"),
        "thread_id": parsed.get("thread_id"),
        "from": parsed.get("from"),
        "from_is_noreply": is_noreply(parsed.get("from") or ""),
        "to": parsed.get("to"),
        "cc": parsed.get("cc"),
        "subject": parsed.get("subject"),
        "date": parsed.get("date"),
        "message_id_header": parsed.get("message_id_header"),
        "references": parsed.get("references"),
        "body": body,
    }, ensure_ascii=False)


GMAIL_READ = Tool(
    name="gmail_read",
    description=(
        "Fetch the full parsed body of a single email. Use after gmail_list "
        "or gmail_search when the user wants details on a specific message "
        "or before composing a reply (so you have the original context)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "Gmail message id from a prior list/search result."},
            "mark_read": {"type": "boolean", "default": True},
        },
        "required": ["message_id"],
    },
    run=_gmail_read,
)


# --- search -----------------------------------------------------------------

async def _gmail_search(args: dict, context: "Context") -> str:
    client, err = _gmail_or_disabled()
    if err:
        return err
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    max_results = int(args.get("max_results") or 10)
    try:
        msgs = client.search(query, max_results=max_results)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    user_id = int(getattr(context, "user_id", 0) or 0)
    items = [
        InboxItem(
            id=m.get("id"),
            thread_id=m.get("thread_id") or "",
            sender=m.get("from") or "",
            subject=m.get("subject") or "",
            snippet=m.get("snippet") or "",
            date=m.get("date") or "",
        )
        for m in msgs
    ]
    remember_inbox(user_id, items)

    return json.dumps({
        "query": query,
        "count": len(items),
        "messages": [
            {
                "id": it.id,
                "from": it.sender,
                "subject": it.subject,
                "snippet": (it.snippet or "")[:200],
                "date": it.date,
            }
            for it in items
        ],
    }, ensure_ascii=False)


GMAIL_SEARCH = Tool(
    name="gmail_search",
    description=(
        "Search Gmail with native operators (from:, to:, subject:, "
        "after:YYYY/MM/DD, before:YYYY/MM/DD, has:attachment, is:unread, "
        "in:sent). Returns matched messages as JSON for the model to filter "
        "and present to the user."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
    },
    run=_gmail_search,
)


# --- send (with default 10-min delayed-send + cancellation) ----------------

async def _gmail_send(args: dict, context: "Context") -> str:
    """Schedule (or fire) a send. Default delay is 10 minutes so the user
    can cancel from Telegram. The agentic loop should follow this with a
    final-text turn that surfaces the cancel hook to the user."""
    client, err = _gmail_or_disabled()
    if err:
        return err
    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = args.get("body") or ""
    if not to or not body:
        return json.dumps({"error": "to and body are required"})
    if not subject:
        subject = "(no subject)"

    user_id = int(getattr(context, "user_id", 0) or 0)
    delay_minutes = int(args.get("delay_minutes", 10))
    delay_seconds = max(0, delay_minutes) * 60

    reply_to_id = (args.get("reply_to_id") or "").strip()
    in_reply_to = None
    references = None
    thread_id = None
    if reply_to_id:
        try:
            orig = client.get_message_parsed(reply_to_id)
            in_reply_to = orig.get("message_id_header")
            references = orig.get("references")
            thread_id = orig.get("thread_id")
        except Exception as exc:
            logger.warning("gmail_send: get_message_parsed(%s) failed: %s", reply_to_id, exc)

    if delay_seconds <= 0:
        try:
            res = client.send(
                to=to, subject=subject, body=body,
                thread_id=thread_id, in_reply_to=in_reply_to, references=references,
            )
            return json.dumps({
                "status": "sent",
                "to": to,
                "subject": subject,
                "message_id": res.get("id"),
            })
        except Exception as exc:
            return json.dumps({"error": f"send failed: {exc}"})

    # Delayed: register a task, hand the send_id back so the model can tell
    # the user how to cancel.
    draft = {
        "to": to,
        "subject": subject,
        "body": body,
        "in_reply_to": in_reply_to,
        "references": references,
        "thread_id": thread_id,
    }

    async def _on_fire(d: dict) -> dict:
        return client.send(
            to=d["to"], subject=d["subject"], body=d["body"],
            thread_id=d.get("thread_id"),
            in_reply_to=d.get("in_reply_to"),
            references=d.get("references"),
        )

    rec = schedule_send(
        user_id=user_id,
        draft=draft,
        delay_seconds=delay_seconds,
        on_fire=_on_fire,
    )

    # Bubble the send_id up to the orchestrator via the context dict so it
    # can attach a Cancel button to the final user-facing message.
    pending_sends = getattr(context, "_tool_side_effects", None)
    if pending_sends is None:
        pending_sends = {}
        setattr(context, "_tool_side_effects", pending_sends)
    pending_sends.setdefault("scheduled_sends", []).append({
        "send_id": rec.id,
        "to": to,
        "delay_minutes": delay_minutes,
    })

    return json.dumps({
        "status": "scheduled",
        "send_id": rec.id,
        "delay_minutes": delay_minutes,
        "to": to,
        "subject": subject,
        "cancel_hint": (
            "User can tap the Cancel button on the next message, or run "
            f"/cancel_email {rec.id} within {delay_minutes} minutes."
        ),
    })


GMAIL_SEND = Tool(
    name="gmail_send",
    description=(
        "Send an email. By default the send is delayed 10 minutes so the "
        "user can cancel — set delay_minutes=0 ONLY when the user explicitly "
        "asked to send immediately. For replies, pass reply_to_id (a Gmail "
        "message id from a prior gmail_list/search/read result); threading "
        "headers are added automatically and the recipient is taken from "
        "the original 'From' if 'to' is the same address. Always include a "
        "concrete subject and body — the model writes the body in the user's "
        "voice. After calling this, write a short final answer to the user "
        "summarising what was scheduled and how to cancel."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Full email body, in the user's voice and language."},
            "reply_to_id": {
                "type": "string",
                "description": "Gmail message id of the email being replied to. Optional; sets threading headers correctly.",
            },
            "delay_minutes": {
                "type": "integer",
                "default": 10,
                "minimum": 0,
                "maximum": 1440,
                "description": "Minutes to delay before sending. 0 = send now (only if user explicitly requested immediate send).",
            },
        },
        "required": ["to", "body"],
    },
    run=_gmail_send,
)


# --- archive / mark-read ----------------------------------------------------

async def _gmail_archive(args: dict, context: "Context") -> str:
    client, err = _gmail_or_disabled()
    if err:
        return err
    msg_id = (args.get("message_id") or "").strip()
    if not msg_id:
        return json.dumps({"error": "message_id is required"})
    try:
        client.archive(msg_id)
        return json.dumps({"status": "archived", "id": msg_id})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


GMAIL_ARCHIVE = Tool(
    name="gmail_archive",
    description="Archive an email (removes the INBOX label). Use when the user explicitly asks to archive or after a send-and-archive flow.",
    input_schema={
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    },
    run=_gmail_archive,
)


async def _gmail_mark_read(args: dict, context: "Context") -> str:
    client, err = _gmail_or_disabled()
    if err:
        return err
    msg_id = (args.get("message_id") or "").strip()
    if not msg_id:
        return json.dumps({"error": "message_id is required"})
    try:
        client.mark_read(msg_id)
        return json.dumps({"status": "marked_read", "id": msg_id})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


GMAIL_MARK_READ = Tool(
    name="gmail_mark_read",
    description="Remove the UNREAD label from an email.",
    input_schema={
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    },
    run=_gmail_mark_read,
)


# --- cancel scheduled send --------------------------------------------------

async def _gmail_cancel_send(args: dict, context: "Context") -> str:
    send_id = (args.get("send_id") or "").strip()
    if not send_id:
        return json.dumps({"error": "send_id is required"})
    if cancel_scheduled(send_id):
        return json.dumps({"status": "cancelled", "send_id": send_id})
    return json.dumps({"status": "already_sent_or_expired", "send_id": send_id})


GMAIL_CANCEL_SEND = Tool(
    name="gmail_cancel_send",
    description="Cancel a previously scheduled gmail_send within its delay window.",
    input_schema={
        "type": "object",
        "properties": {"send_id": {"type": "string"}},
        "required": ["send_id"],
    },
    run=_gmail_cancel_send,
)


def build_email_tools() -> list[Tool]:
    """Return the email tool primitives — but only when Gmail is configured.
    Hides the whole layer otherwise so the model doesn't promise capabilities
    it can't deliver."""
    try:
        client = GmailClient()
        if not client.enabled:
            return []
    except Exception:
        return []
    return [
        GMAIL_LIST, GMAIL_READ, GMAIL_SEARCH,
        GMAIL_SEND, GMAIL_ARCHIVE, GMAIL_MARK_READ, GMAIL_CANCEL_SEND,
    ]
