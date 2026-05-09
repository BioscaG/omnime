"""Bot application factory and runner."""
from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.bot import callbacks, commands, handlers, middleware
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
    ("export", "Export your data"),
    ("skills", "List OMNIME capabilities"),
    ("evolve", "Add a new capability"),
    ("settings", "Configure OMNIME"),
    ("backup", "Create a backup"),
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

    application.add_handler(CallbackQueryHandler(callbacks.handle_callback))
    application.add_handler(MessageHandler(filters.VOICE, handlers.handle_voice))
    application.add_handler(MessageHandler(filters.Document.ALL, handlers.handle_document))
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

    job_queue.run_daily(briefing_job, time=dtime(hour=hh, minute=mm))
    job_queue.run_repeating(profile_refresh_job, interval=60 * 60 * 24, first=60 * 60)


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
