"""Bot application factory and runner."""
from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from src.bot import callbacks, commands, handlers, inline, middleware
from src.brain.context_builder import ContextBuilder
from src.brain.llm_client import LLMClient
from src.brain.orchestrator import Orchestrator
from src.config import settings
from src.memory.manager import MemoryManager
from src.skills.registry import get_registry


logger = logging.getLogger(__name__)


COMMAND_DESCRIPTIONS = [
    ("start", "Initialize OMNIME"),
    ("me", "Show everything OMNIME knows about you"),
    ("search", "Search your memory"),
    ("projects", "List your projects"),
    ("cv", "Generate your CV"),
    ("cv_for", "Generate CV tailored to a job posting"),
    ("email", "Compose an email"),
    ("briefing", "Get your daily briefing"),
    ("review", "Run your weekly review"),
    ("goal", "Track a new goal with a streak"),
    ("forget", "Delete a memory entry with audit"),
    ("private", "Send a one-off message via local Ollama"),
    ("export", "Export your data"),
    ("skills", "List OMNIME capabilities"),
    ("evolve", "Add a new capability"),
    ("settings", "Configure OMNIME"),
    ("backup", "Create a backup"),
    ("usage", "Show LLM token usage and estimated cost"),
]


async def _post_init(application: Application) -> None:
    cmds = [BotCommand(c, d) for c, d in COMMAND_DESCRIPTIONS]
    try:
        await application.bot.set_my_commands(cmds)
    except Exception as exc:
        logger.warning("Could not set bot commands: %s", exc)


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    builder = ApplicationBuilder().token(settings.telegram_bot_token).post_init(_post_init)
    application = builder.build()

    llm = LLMClient()
    memory = MemoryManager(llm=llm)
    user_id_db = memory.ensure_user(telegram_id=settings.telegram_user_id)
    skill_registry = get_registry(llm=llm, memory=memory)

    evolution_engine = None
    try:
        from src.evolution.self_coder import EvolutionEngine

        evolution_engine = EvolutionEngine(llm=llm, memory=memory, registry=skill_registry)
    except Exception as exc:
        logger.warning("Evolution engine disabled: %s", exc)

    orchestrator = Orchestrator(
        llm=llm,
        memory=memory,
        skill_registry=skill_registry,
        evolution_engine=evolution_engine,
    )

    application.bot_data["llm"] = llm
    application.bot_data["memory"] = memory
    application.bot_data["user_id_db"] = user_id_db
    application.bot_data["context_builder"] = ContextBuilder(memory)
    application.bot_data["skill_registry"] = skill_registry
    application.bot_data["orchestrator"] = orchestrator
    application.bot_data["evolution_engine"] = evolution_engine

    middleware.install(application)

    application.add_handler(CommandHandler("start", commands.cmd_start))
    application.add_handler(CommandHandler("me", commands.cmd_me))
    application.add_handler(CommandHandler("search", commands.cmd_search))
    application.add_handler(CommandHandler("projects", commands.cmd_projects))
    application.add_handler(CommandHandler("cv", commands.cmd_cv))
    application.add_handler(CommandHandler("cv_for", commands.cmd_cv_for))
    application.add_handler(CommandHandler("email", commands.cmd_email))
    application.add_handler(CommandHandler("briefing", commands.cmd_briefing))
    application.add_handler(CommandHandler("skills", commands.cmd_skills))
    application.add_handler(CommandHandler("evolve", commands.cmd_evolve))
    application.add_handler(CommandHandler("settings", commands.cmd_settings))
    application.add_handler(CommandHandler("export", commands.cmd_export))
    application.add_handler(CommandHandler("backup", commands.cmd_backup))
    application.add_handler(CommandHandler("forget", commands.cmd_forget))
    application.add_handler(CommandHandler("review", commands.cmd_review))
    application.add_handler(CommandHandler("goal", commands.cmd_goal))
    application.add_handler(CommandHandler("private", commands.cmd_private))
    application.add_handler(CommandHandler("usage", commands.cmd_usage))
    application.add_handler(CommandHandler("voice", commands.cmd_voice_reply))
    application.add_handler(CommandHandler("plan", commands.cmd_plan))
    application.add_handler(CommandHandler("agent", commands.cmd_plan))

    application.add_handler(CallbackQueryHandler(callbacks.handle_callback))
    application.add_handler(InlineQueryHandler(inline.handle_inline_query))
    application.add_handler(MessageHandler(filters.VOICE, handlers.handle_voice))
    application.add_handler(MessageHandler(filters.Document.ALL, handlers.handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, handlers.handle_photo))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text)
    )

    _schedule_jobs(application, memory, llm, user_id_db)

    return application


def _schedule_jobs(application: Application, memory: MemoryManager, llm: LLMClient, user_id_db: int) -> None:
    from datetime import time as dtime

    job_queue = application.job_queue
    if job_queue is None:
        return

    try:
        hh, mm = (int(x) for x in settings.daily_briefing_time.split(":"))
    except Exception:
        hh, mm = 8, 0

    async def briefing_job(context):
        try:
            from src.brain.context_builder import ContextBuilder

            ctx = await ContextBuilder(memory).build(user_id_db, "/briefing")
            skill = application.bot_data["skill_registry"].get("daily_briefing")
            if skill is None:
                return
            sr = await skill.execute("/briefing", ctx)
            await context.bot.send_message(chat_id=settings.telegram_user_id, text=sr.text[:4000])
        except Exception as exc:
            logger.warning("Daily briefing job failed: %s", exc)

    async def profile_refresh_job(context):
        try:
            await memory.summarizer.update_living_profile(user_id_db)
        except Exception as exc:
            logger.warning("Living profile refresh failed: %s", exc)

    async def memory_maintenance_job(context):
        try:
            stats = memory.run_maintenance(user_id_db)
            logger.info("Memory maintenance: %s", stats)
        except Exception as exc:
            logger.warning("Memory maintenance failed: %s", exc)

    async def weekly_review_job(context):
        try:
            from src.brain.context_builder import ContextBuilder

            ctx = await ContextBuilder(memory).build(user_id_db, "/review")
            skill = application.bot_data["skill_registry"].get("weekly_review")
            if skill is None:
                return
            sr = await skill.execute("/review", ctx)
            await context.bot.send_message(chat_id=settings.telegram_user_id, text=sr.text[:4000])
        except Exception as exc:
            logger.warning("Weekly review job failed: %s", exc)

    async def birthday_reminder_job(context):
        try:
            from datetime import date as _date

            today = _date.today()
            with __import__("src.memory.db", fromlist=["session_scope"]).session_scope() as s:
                from sqlalchemy import select
                from src.memory import models as mm

                contacts = list(s.scalars(select(mm.Contact).where(mm.Contact.user_id == user_id_db)))
                upcoming = []
                for c in contacts:
                    md = c.extra_metadata or {}
                    bday_iso = md.get("birthday")
                    if not bday_iso:
                        continue
                    try:
                        from datetime import datetime as _dt

                        bday = _dt.fromisoformat(bday_iso).date()
                    except ValueError:
                        continue
                    next_occurrence = bday.replace(year=today.year)
                    if next_occurrence < today:
                        next_occurrence = next_occurrence.replace(year=today.year + 1)
                    delta = (next_occurrence - today).days
                    if delta <= 7:
                        upcoming.append((delta, c.name, next_occurrence))
            if upcoming:
                upcoming.sort()
                lines = "\n".join(f"• {n} — {d.isoformat()} (in {days}d)" for days, n, d in upcoming)
                await context.bot.send_message(
                    chat_id=settings.telegram_user_id,
                    text=f"🎂 Upcoming birthdays:\n{lines}",
                )
        except Exception as exc:
            logger.warning("Birthday reminder failed: %s", exc)

    async def notion_sync_job(context):
        try:
            from src.integrations.notion_client import NotionClient

            client = NotionClient()
            if not client.enabled:
                return
            from src.integrations.notion_sync import NotionSync

            stats = await NotionSync(memory, client).push_all(user_id_db)
            logger.info("Notion sync: %s", stats)
        except Exception as exc:
            logger.warning("Notion sync failed: %s", exc)

    job_queue.run_daily(briefing_job, time=dtime(hour=hh, minute=mm))
    job_queue.run_repeating(profile_refresh_job, interval=60 * 60 * 24, first=60 * 60)
    job_queue.run_repeating(memory_maintenance_job, interval=60 * 60 * 24, first=60 * 60 * 2)
    # Sunday 19:00 local time review
    from datetime import time as dtime_

    job_queue.run_daily(weekly_review_job, time=dtime_(hour=19, minute=0), days=(6,))
    job_queue.run_repeating(notion_sync_job, interval=60 * 60 * 6, first=60 * 30)
    job_queue.run_daily(birthday_reminder_job, time=dtime_(hour=8, minute=15))


def run() -> None:
    application = build_application()
    if settings.telegram_mode == "webhook" and settings.webhook_url:
        logger.info("Starting in webhook mode: %s", settings.webhook_url)
        application.run_webhook(
            listen="0.0.0.0",
            port=settings.webhook_port,
            url_path=settings.telegram_bot_token,
            webhook_url=f"{settings.webhook_url.rstrip('/')}/{settings.telegram_bot_token}",
        )
    else:
        logger.info("Starting in polling mode")
        application.run_polling(allowed_updates=["message", "callback_query"])
