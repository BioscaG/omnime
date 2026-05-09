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
    await query.edit_message_text(f"Action received: {data}")


async def _handle_email_callback(query, context, data: str) -> None:
    action = data.split(":", 1)[1]
    if action == "send":
        try:
            from src.integrations.gmail_client import GmailClient

            client = GmailClient()
            if not client.enabled:
                await query.edit_message_text("Gmail not configured. Cannot send.")
                return
            await query.edit_message_text("Drafted only. Send-from-bot requires more context (recipient).")
        except Exception as exc:
            logger.exception("Email send failed")
            await query.edit_message_text(f"Failed: {exc}")
    elif action == "cancel":
        await query.edit_message_text("Cancelled.")
    else:
        await query.edit_message_text(f"Action: {action}")


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
