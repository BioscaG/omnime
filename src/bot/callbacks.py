"""Inline-button callback handlers (mainly for confirm/cancel flows)."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.middleware import authorize


logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""

    if data.startswith("email:"):
        await _handle_email_callback(query, context, data)
        return
    if data.startswith("evolve:"):
        await _handle_evolve_callback(query, context, data)
        return
    if data.startswith("browse:"):
        await _handle_browse_callback(query, context, data)
        return
    await query.edit_message_text(f"Action received: {data}")


async def _handle_browse_callback(query, context, data: str) -> None:
    action = data.split(":", 1)[1]
    state = context.application.bot_data.get("browser_pending", {})
    user_id_db = context.application.bot_data["user_id_db"]
    pending = state.pop(user_id_db, None)

    if action == "cancel" or pending is None:
        await query.edit_message_text("🛑 Browser session cancelled.")
        return
    if action == "edit":
        await query.edit_message_text(
            "Reply with a new instruction starting with /browse to redirect the agent."
        )
        return
    if action == "continue":
        # Re-launch the agent with augmented goal so it carries past the confirmation gate.
        await query.edit_message_text("✅ Resuming…")
        from src.bot.commands import cmd_browse  # noqa: E402

        # Synthesize the equivalent of /browse <goal>. Reusing the command path
        # keeps confirmation logic centralised.
        original_args = (pending["goal"] + " (resume past confirmation)").split()
        context.args = original_args
        await cmd_browse(query.message, context)


# --- Email -----------------------------------------------------------------

# delay presets in seconds for scheduled-send
SEND_DELAYS = {
    "now": 0,
    "10m": 10 * 60,
    "1h": 60 * 60,
}


async def _handle_email_callback(query, context, data: str) -> None:
    """Dispatch ``email:<action>[:<extra>]`` callbacks."""
    parts = data.split(":")
    # parts[0] = "email"
    action = parts[1] if len(parts) > 1 else ""

    user_id_db = context.application.bot_data.get("user_id_db") or 0

    # ---- Send / Schedule ---------------------------------------------------
    if action == "send":
        delay_key = parts[2] if len(parts) > 2 else "10m"
        await _email_send(query, context, user_id_db, delay_key)
        return

    if action == "scheduled_cancel":
        send_id = parts[2] if len(parts) > 2 else ""
        from src.skills.email_state import cancel_scheduled

        if cancel_scheduled(send_id):
            await query.edit_message_text("🛑 Scheduled send cancelled.")
        else:
            await query.edit_message_text(
                "Already sent or expired — nothing to cancel."
            )
        return

    if action == "edit":
        await query.edit_message_text(
            "✏️ Tell me the changes ('make it more formal', 'shorten it', "
            "'add a closing about the meeting Tuesday') and I'll re-draft."
        )
        return

    if action == "cancel":
        from src.skills.email_state import pop_draft

        pop_draft(user_id_db)
        await query.edit_message_text("❌ Draft discarded.")
        return

    # ---- Inline actions on a specific message: read / reply / archive ------
    if action == "read":
        message_id = parts[2] if len(parts) > 2 else ""
        await _email_open(query, context, user_id_db, message_id)
        return

    if action == "reply":
        message_id = parts[2] if len(parts) > 2 else ""
        await _email_start_reply(query, context, user_id_db, message_id)
        return

    if action == "forward":
        message_id = parts[2] if len(parts) > 2 else ""
        await query.edit_message_text(
            f"📤 Forward {message_id}: tell me _who_ to forward it to "
            "(e.g. 'reenvíaselo a marc@x.com')."
        )
        return

    if action == "archive":
        message_id = parts[2] if len(parts) > 2 else ""
        await _email_archive(query, message_id)
        return

    await query.edit_message_text(f"Email action: {action}")


async def _email_send(query, context, user_id_db: int, delay_key: str) -> None:
    """Schedule (or fire immediately) a stashed draft via Gmail."""
    delay = SEND_DELAYS.get(delay_key, SEND_DELAYS["10m"])
    from src.integrations.gmail_client import GmailClient
    from src.skills.email_state import peek_draft, pop_draft, schedule_send

    draft = peek_draft(user_id_db)
    if draft is None:
        await query.edit_message_text(
            "No pending draft — start with `/email <instruction>` first."
        )
        return
    if not draft.get("to"):
        await query.edit_message_text(
            "Recipient is missing. Reply with the email address and I'll re-draft."
        )
        return

    client = GmailClient()
    if not client.enabled:
        await query.edit_message_text(
            "Gmail isn't connected. Set the `GMAIL_*` env vars and try again."
        )
        return

    if delay <= 0:
        # Immediate fire — pop the draft, send synchronously.
        pop_draft(user_id_db)
        try:
            res = client.send(
                to=draft["to"],
                subject=draft["subject"],
                body=draft["body"],
                thread_id=draft.get("thread_id"),
                in_reply_to=draft.get("in_reply_to"),
                references=draft.get("references"),
            )
            await query.edit_message_text(
                f"📨 Sent to **{draft['to']}** (id `{res.get('id', '?')}`)."
            )
        except Exception as exc:
            logger.exception("immediate email send failed")
            await query.edit_message_text(f"⚠️ Send failed: {exc}")
        return

    # Delayed fire — pop the draft and register an asyncio task.
    draft_copy = dict(draft)
    pop_draft(user_id_db)

    chat_id = query.message.chat_id if query.message else None
    bot = context.bot

    async def _on_fire(d: dict) -> dict:
        return client.send(
            to=d["to"],
            subject=d["subject"],
            body=d["body"],
            thread_id=d.get("thread_id"),
            in_reply_to=d.get("in_reply_to"),
            references=d.get("references"),
        )

    async def _on_complete(send_id: str, result, err) -> None:
        if chat_id is None:
            return
        try:
            if err is None:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📨 Sent (delayed) to **{draft_copy['to']}**.",
                    parse_mode="Markdown",
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Scheduled send failed: {err}",
                )
        except Exception:
            logger.exception("post-send notify failed")

    rec = schedule_send(
        user_id=user_id_db,
        draft=draft_copy,
        delay_seconds=delay,
        on_fire=_on_fire,
        on_complete=_on_complete,
    )

    nice_when = {"10m": "10 minutes", "1h": "1 hour"}.get(delay_key, f"{int(delay)}s")
    await query.edit_message_text(
        f"⏰ Scheduled to send in **{nice_when}**. Tap below to cancel.",
        reply_markup=_inline([
            [{"text": "❌ Cancel scheduled send", "callback_data": f"email:scheduled_cancel:{rec.id}"}]
        ]),
    )


async def _email_open(query, context, user_id_db: int, message_id: str) -> None:
    """Reuse the email_read skill so we get the same render the user would
    see if they typed `/read X`."""
    if not message_id:
        await query.edit_message_text("No message id supplied.")
        return
    registry = context.application.bot_data.get("skill_registry")
    context_builder = context.application.bot_data.get("context_builder")
    if registry is None or context_builder is None:
        await query.edit_message_text("Skill registry not initialised.")
        return
    skill = registry.get("email_read")
    if skill is None:
        await query.edit_message_text("Read skill unavailable.")
        return

    ctx = await context_builder.build(user_id_db, f"/read {message_id}")
    sr = await skill.execute(message=f"/read {message_id}", context=ctx)
    await query.message.reply_text(
        sr.text,
        reply_markup=_inline(sr.inline_buttons) if sr.inline_buttons else None,
        parse_mode="Markdown",
    )


async def _email_start_reply(query, context, user_id_db: int, message_id: str) -> None:
    if not message_id:
        await query.edit_message_text("No message id supplied.")
        return
    # Stash the message id as the "last opened" so the composer picks it up.
    from src.skills.email_state import remember_opened

    remember_opened(user_id_db, message_id, "")
    await query.edit_message_text(
        "↩️ Tell me what to say in the reply (e.g. _'thanks, I'll review and "
        "get back this afternoon'_) and I'll draft it."
    )


async def _email_archive(query, message_id: str) -> None:
    from src.integrations.gmail_client import GmailClient

    client = GmailClient()
    if not client.enabled:
        await query.edit_message_text("Gmail not connected.")
        return
    try:
        client.archive(message_id)
        await query.edit_message_text("🗑 Archived.")
    except Exception as exc:
        logger.exception("archive failed")
        await query.edit_message_text(f"⚠️ Archive failed: {exc}")


def _inline(rows: list[list[dict[str, str]]]):
    """Build a Telegram InlineKeyboardMarkup from our dict-of-buttons format."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
        for row in rows
    ]
    return InlineKeyboardMarkup(keyboard)


async def _handle_evolve_callback(query, context, data: str) -> None:
    action = data.split(":", 1)[1]
    if action == "approve":
        engine = context.application.bot_data.get("evolution_engine")
        if engine is None:
            await query.edit_message_text("Evolution engine unavailable.")
            return
        try:
            res = await engine.approve_pending(context.application.bot_data["user_id_db"])
            await query.edit_message_text(f"Deployed: {res}")
        except Exception as exc:
            logger.exception("Evolution approval failed")
            await query.edit_message_text(f"Failed: {exc}")
    elif action == "reject":
        engine = context.application.bot_data.get("evolution_engine")
        if engine:
            engine.reject_pending(context.application.bot_data["user_id_db"])
        await query.edit_message_text("Discarded.")
    else:
        await query.edit_message_text(f"Action: {action}")
