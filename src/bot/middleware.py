"""Bot middleware: auth gate, rate limiting and global error logging."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import Application, ContextTypes

from src.config import settings
from src.utils.rate_limiter import RateLimiter


logger = logging.getLogger(__name__)
_rate_limiter = RateLimiter(max_per_minute=30)


async def authorize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Silently drop messages that don't come from the configured user."""
    user = update.effective_user
    if user is None:
        return False
    if settings.telegram_user_id and user.id != settings.telegram_user_id:
        logger.warning("Rejected message from unauthorized user %s", user.id)
        return False
    return True


async def rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user is None:
        return False
    allowed = _rate_limiter.allow(str(user.id))
    if not allowed:
        if update.effective_message:
            await update.effective_message.reply_text(
                "⏳ Too many requests, slow down a moment."
            )
    return allowed


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    import traceback as _tb

    err = context.error
    tb_text = "".join(_tb.format_exception(type(err), err, err.__traceback__)) if err else ""
    logger.error("Bot error: %s\n%s", err, tb_text)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong on my side. The error has been logged."
            )
    except Exception as exc:
        logger.error("Failed to send error reply: %s", exc)


def install(application: Application) -> None:
    application.add_error_handler(error_handler)
