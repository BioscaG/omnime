"""Reminder dispatcher — fires due reminders to the user via Telegram.

Runs every 2 minutes (via APScheduler). Picks up undelivered reminders
whose due_at has passed, sends a friendly ping per item, and marks them
delivered. Cooperates with the proactive scanner but is dedicated to
explicit reminders set via the ``memory_remind`` primitive.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select

from src.memory import models as m
from src.memory.db import session_scope


logger = logging.getLogger(__name__)


async def fire_due_reminders(application, user_id: int) -> int:
    """Send any reminders whose due_at has passed for this user.
    Returns count fired."""
    with session_scope() as s:
        rows = s.execute(
            select(m.Reminder)
            .where(m.Reminder.user_id == user_id)
            .where(m.Reminder.delivered_at.is_(None))
            .where(m.Reminder.due_at <= datetime.utcnow())
            .order_by(m.Reminder.due_at)
            .limit(10)
        ).scalars().all()

        if not rows:
            return 0

        from src.utils.formatters import to_telegram_html
        from telegram.constants import ParseMode

        from src.config import settings
        chat_id = (
            settings.proactive_chat_id
            or settings.telegram_user_id
            or (application.bot_data or {}).get("primary_chat_id")
            or 0
        )
        if not chat_id:
            logger.warning("reminders: no chat_id configured; skipping fire")
            return 0

        fired = 0
        for r in rows:
            try:
                msg = f"⏰ <b>Recordatorio</b>\n\n{r.content}"
                if r.context:
                    msg += f"\n\n<i>Contexto: {r.context[:200]}</i>"
                await application.bot.send_message(
                    chat_id=chat_id, text=to_telegram_html(msg),
                    parse_mode=ParseMode.HTML,
                )
                r.delivered_at = datetime.utcnow()
                fired += 1
                logger.info("reminder fired: id=%d content=%s", r.id, (r.content or "")[:80])
            except Exception as exc:
                logger.warning("reminder fire failed (id=%d): %s", r.id, exc)
        return fired
