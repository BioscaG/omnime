"""Slash command handlers."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.bot.middleware import authorize, rate_limit
from src.config import settings


logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    name = update.effective_user.first_name or "there"
    text = (
        f"👋 Hi {name}, I'm OMNIME — your personal AI assistant.\n\n"
        "Tell me anything about yourself, your work, your ideas. I'll remember it.\n"
        "I can also generate CVs, draft emails, write documents, research topics,\n"
        "and give you a daily briefing.\n\n"
        "Try /me to see what I know, /skills for my capabilities, /search to query memory."
    )
    await update.effective_message.reply_text(text)


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    profile = memory.get_user_profile(user_id_db)
    if not profile:
        await update.effective_message.reply_text("I don't know much about you yet.")
        return
    parts = [f"*{profile.get('name') or 'Profile'}*"]
    if profile.get("living_profile"):
        parts.append(profile["living_profile"][:1500])
    elif profile.get("bio"):
        parts.append(profile["bio"][:1500])
    if profile.get("projects"):
        parts.append("\n*Projects:*")
        for p in profile["projects"][:8]:
            parts.append(f"• {p['name']} ({p.get('status')})")
    if profile.get("skills"):
        sk = ", ".join(s["name"] for s in profile["skills"][:20])
        parts.append(f"\n*Skills:* {sk}")
    if profile.get("work_experience"):
        parts.append("\n*Work:*")
        for w in profile["work_experience"][:5]:
            parts.append(f"• {w['role']} @ {w['company']}")
    text = "\n".join(parts)
    try:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.effective_message.reply_text(text)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /search <query>")
        return
    query = " ".join(context.args)
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    hits = memory.semantic_search(user_id_db, query, n_results=8)
    if not hits:
        await update.effective_message.reply_text("No matches found.")
        return
    lines = ["*Top matches:*"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h.text[:300]}")
    text = "\n\n".join(lines)
    await update.effective_message.reply_text(text[:4000])


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    profile = memory.get_user_profile(user_id_db)
    projects = profile.get("projects") or []
    if not projects:
        await update.effective_message.reply_text("No projects stored yet.")
        return
    lines = []
    for p in projects:
        tech = ", ".join(p.get("technologies") or []) or "—"
        lines.append(f"• *{p['name']}* ({p.get('status')}) — {p.get('description') or ''}\n  tech: {tech}")
    await update.effective_message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_cv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_skill(update, context, "cv_generator", "/cv")


async def cmd_cv_for(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    await _run_skill(update, context, "cv_generator", text)


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or "/email"
    await _run_skill(update, context, "email_composer", text)


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_skill(update, context, "daily_briefing", "/briefing")


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    registry = context.application.bot_data["skill_registry"]
    skills = registry.list_skills()
    lines = ["*Capabilities:*"]
    for s in skills:
        lines.append(f"• `{s.name}` — {s.description}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_evolve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /evolve <description of new capability>"
        )
        return
    request = " ".join(context.args)
    orchestrator = context.application.bot_data["orchestrator"]
    user_id_db = context.application.bot_data["user_id_db"]
    response = await orchestrator.process_message(user_id_db, "EVOLVE: " + request)
    await update.effective_message.reply_text(response.text[:4000])


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    s = settings
    text = (
        "*Settings*\n"
        f"Provider: `{s.llm_provider}` (fast: `{s.llm_model_fast}`, powerful: `{s.llm_model_powerful}`)\n"
        f"Timezone: `{s.timezone}`\n"
        f"Daily briefing: `{s.daily_briefing_time}`\n"
        f"Language: `{s.language}`\n"
        f"Gmail: {'on' if s.gmail_refresh_token else 'off'}\n"
        f"Calendar: {'on' if s.gcal_refresh_token else 'off'}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    profile = memory.get_user_profile(user_id_db)

    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = settings.exports_dir / f"export_{ts}.json"
    path.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")

    with path.open("rb") as fh:
        await update.effective_chat.send_document(document=fh, filename=path.name)


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    from scripts.backup import run_backup

    try:
        backup_path = run_backup()
        await update.effective_message.reply_text(f"Backup written: `{backup_path}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        logger.exception("Backup failed")
        await update.effective_message.reply_text(f"Backup failed: {exc}")


async def _run_skill(update: Update, context: ContextTypes.DEFAULT_TYPE, skill_name: str, message: str) -> None:
    if not await authorize(update, context):
        return
    if not await rate_limit(update, context):
        return
    registry = context.application.bot_data["skill_registry"]
    context_builder = context.application.bot_data["context_builder"]
    user_id_db = context.application.bot_data["user_id_db"]

    skill = registry.get(skill_name)
    if skill is None:
        await update.effective_message.reply_text(f"Skill not available: {skill_name}")
        return

    ctx = await context_builder.build(user_id_db, message)
    sr = await skill.execute(message, ctx)
    chat = update.effective_chat
    if sr.text:
        await chat.send_message(sr.text[:4000])
    if sr.inline_buttons:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
            for row in sr.inline_buttons
        ]
        await chat.send_message("Choose:", reply_markup=InlineKeyboardMarkup(keyboard))
    for f in sr.files or []:
        from pathlib import Path

        p = Path(f["path"])
        if p.exists():
            with p.open("rb") as fh:
                await chat.send_document(document=fh, filename=p.name)
