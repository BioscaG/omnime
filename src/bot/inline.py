"""Inline mode: `@omnime <query>` from any Telegram chat returns drafts."""
from __future__ import annotations

import hashlib
import logging

from telegram import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.ext import ContextTypes

from src.config import settings


logger = logging.getLogger(__name__)


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_obj = update.inline_query
    if query_obj is None:
        return
    if settings.telegram_user_id and query_obj.from_user.id != settings.telegram_user_id:
        await query_obj.answer([], cache_time=1, is_personal=True)
        return

    query = (query_obj.query or "").strip()
    if not query:
        await query_obj.answer([_help_result()], cache_time=10, is_personal=True)
        return

    orchestrator = context.application.bot_data["orchestrator"]
    user_id_db = context.application.bot_data["user_id_db"]

    results = []
    for variant_label, prefix in (
        ("Email draft", "draft a short email about: "),
        ("LinkedIn post", "draft a LinkedIn post about: "),
        ("One-pager", "write a one-pager on: "),
    ):
        try:
            response = await orchestrator.process_message(
                user_id=user_id_db, message=prefix + query,
            )
            preview = (response.text or "")[:300]
            article_id = hashlib.sha1((variant_label + query).encode()).hexdigest()
            results.append(
                InlineQueryResultArticle(
                    id=article_id,
                    title=variant_label,
                    description=preview[:120],
                    input_message_content=InputTextMessageContent(
                        message_text=response.text[:4000] or preview,
                    ),
                )
            )
        except Exception as exc:
            logger.warning("Inline variant %s failed: %s", variant_label, exc)

    if not results:
        results = [_fallback_result(query)]
    await query_obj.answer(results, cache_time=15, is_personal=True)


def _help_result() -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id="help",
        title="Type to search or generate",
        description="Email drafts · LinkedIn posts · one-pagers",
        input_message_content=InputTextMessageContent(
            message_text="Use @omnime <topic> to draft content from this chat.",
        ),
    )


def _fallback_result(query: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id="fallback",
        title="Send as plain text",
        description=query[:120],
        input_message_content=InputTextMessageContent(message_text=query),
    )
