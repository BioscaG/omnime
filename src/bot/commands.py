"""Slash command handlers."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.middleware import authorize, rate_limit
from src.config import settings
from src.utils.formatters import chunk, safe_send


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
    await safe_send(update.effective_message.reply_text, text)


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    profile = memory.get_user_profile(user_id_db)
    if not profile:
        await safe_send(update.effective_message.reply_text, "I don't know much about you yet.")
        return
    parts = [f"**{profile.get('name') or 'Profile'}**"]
    if profile.get("living_profile"):
        parts.append(profile["living_profile"][:1500])
    elif profile.get("bio"):
        parts.append(profile["bio"][:1500])
    if profile.get("projects"):
        parts.append("\n**Projects:**")
        for p in profile["projects"][:8]:
            parts.append(f"• {p['name']} ({p.get('status')})")
    if profile.get("skills"):
        sk = ", ".join(s["name"] for s in profile["skills"][:20])
        parts.append(f"\n**Skills:** {sk}")
    if profile.get("work_experience"):
        parts.append("\n**Work:**")
        for w in profile["work_experience"][:5]:
            parts.append(f"• {w['role']} @ {w['company']}")
    text = "\n".join(parts)
    for piece in chunk(text, size=3500):
        await safe_send(update.effective_message.reply_text, piece)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(update.effective_message.reply_text, "Usage: /search <query>")
        return
    query = " ".join(context.args)
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    hits = memory.semantic_search(user_id_db, query, n_results=8)
    if not hits:
        await safe_send(update.effective_message.reply_text, "No matches found.")
        return
    lines = ["**Top matches:**"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h.text[:300]}")
    text = "\n\n".join(lines)
    await safe_send(update.effective_message.reply_text, text[:4000])


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    profile = memory.get_user_profile(user_id_db)
    projects = profile.get("projects") or []
    if not projects:
        await safe_send(update.effective_message.reply_text, "No projects stored yet.")
        return
    lines = []
    for p in projects:
        tech = ", ".join(p.get("technologies") or []) or "—"
        lines.append(f"• **{p['name']}** ({p.get('status')}) — {p.get('description') or ''}\n  tech: {tech}")
    await safe_send(update.effective_message.reply_text, "\n\n".join(lines))


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


async def cmd_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List + summarise unread Gmail messages."""
    text = update.effective_message.text or "/inbox"
    await _run_skill(update, context, "email_inbox", text)


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    registry = context.application.bot_data["skill_registry"]
    skills = registry.list_skills()
    lines = ["**Capabilities:**"]
    for s in skills:
        lines.append(f"• `{s.name}` — {s.description}")
    await safe_send(update.effective_message.reply_text, "\n".join(lines))


async def cmd_evolve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(
            update.effective_message.reply_text,
            "Usage: /evolve <description of new capability>",
        )
        return
    request = " ".join(context.args)
    orchestrator = context.application.bot_data["orchestrator"]
    user_id_db = context.application.bot_data["user_id_db"]
    response = await orchestrator.process_message(user_id_db, "EVOLVE: " + request)
    await safe_send(update.effective_message.reply_text, response.text[:4000])


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    s = settings
    text = (
        "**Settings**\n"
        f"Provider: `{s.llm_provider}` (fast: `{s.llm_model_fast}`, powerful: `{s.llm_model_powerful}`)\n"
        f"Timezone: `{s.timezone}`\n"
        f"Daily briefing: `{s.daily_briefing_time}`\n"
        f"Language: `{s.language}`\n"
        f"Gmail: {'on' if s.gmail_refresh_token else 'off'}\n"
        f"Calendar: {'on' if s.gcal_refresh_token else 'off'}\n"
        f"Notion: {'on' if s.notion_token else 'off'}"
    )
    await safe_send(update.effective_message.reply_text, text)


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
        await safe_send(
            update.effective_message.reply_text,
            f"Backup written: `{backup_path}`",
        )
    except Exception as exc:
        logger.exception("Backup failed")
        await safe_send(update.effective_message.reply_text, f"Backup failed: {exc}")


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(
            update.effective_message.reply_text,
            "Usage: /forget <selector>\n"
            "Examples:\n"
            "  /forget project ATLAS\n"
            "  /forget contact Sarah Chen\n"
            "  /forget skill Rust\n"
            "  /forget memory <semantic id>",
        )
        return
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    selector = " ".join(context.args)
    summary = memory.forget(user_id_db, selector)
    await safe_send(update.effective_message.reply_text, summary)


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_skill(update, context, "weekly_review", "/review")


async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(
            update.effective_message.reply_text,
            "Usage: /goal <description>\nExample: /goal run 3x a week",
        )
        return
    description = " ".join(context.args)
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    g = memory.add_goal(user_id_db, description)
    await safe_send(
        update.effective_message.reply_text,
        f"Goal tracked: **{g['description']}** — current streak {g['streak']}",
    )


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the agentic multi-step planner."""
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(update.effective_message.reply_text, "Usage: /plan <goal>")
        return
    goal = " ".join(context.args)
    await _run_skill(update, context, "agentic", "/plan " + goal)


async def cmd_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch a public URL and process it."""
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(update.effective_message.reply_text, "Usage: /fetch <url>")
        return
    url = " ".join(context.args)
    await _run_skill(update, context, "web_fetch", "/fetch " + url)


async def cmd_browse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drive a real browser to advance a goal, with screenshots + confirmations."""
    logger.info("cmd_browse: entered")
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(
            update.effective_message.reply_text,
            "Usage: /browse <goal>\n"
            "Example: /browse busca tren Madrid-Barcelona mañana 12:00 en renfe.com",
        )
        return
    goal = " ".join(context.args)
    logger.info("cmd_browse: goal=%r", goal)

    registry = context.application.bot_data["skill_registry"]
    context_builder = context.application.bot_data["context_builder"]
    user_id_db = context.application.bot_data["user_id_db"]
    skill = registry.get("browser_agent")
    if skill is None:
        await safe_send(update.effective_message.reply_text, "Browser agent not available.")
        return

    chat = update.effective_chat
    ctx = await context_builder.build(user_id_db, "/browse " + goal)
    await safe_send(chat.send_message, f"🌐 Starting browser session\nGoal: {goal}")

    try:
        logger.info("cmd_browse: starting iter_actions")
        async for event in skill.iter_actions("/browse " + goal, ctx):
            logger.info("cmd_browse: event kind=%s text=%r", event.kind, (event.text or "")[:120])
            if event.screenshot and event.screenshot.exists():
                with event.screenshot.open("rb") as fh:
                    await chat.send_photo(photo=fh, caption=event.text[:1000])
            else:
                await safe_send(chat.send_message, event.text or "(no text)")

            if event.kind == "needs_confirmation":
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                state = context.application.bot_data.setdefault("browser_pending", {})
                state[user_id_db] = {"goal": goal, "step": event.step}
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Continue", callback_data="browse:continue"),
                    InlineKeyboardButton("✏️ Edit", callback_data="browse:edit"),
                    InlineKeyboardButton("❌ Cancel", callback_data="browse:cancel"),
                ]])
                await chat.send_message(
                    "Awaiting your decision before continuing.", reply_markup=kb,
                )
                return
            if event.kind in ("done", "error"):
                return
    except Exception as exc:
        logger.exception("Browser session crashed")
        await safe_send(chat.send_message, f"Browser crash: {exc}")


async def cmd_creds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage encrypted site credentials."""
    if not await authorize(update, context):
        return
    args = context.args or []
    user_id_db = context.application.bot_data["user_id_db"]

    from src.utils.credentials import CredentialVault

    if not args:
        sites = CredentialVault.list_sites(user_id_db)
        if not sites:
            await safe_send(
                update.effective_message.reply_text,
                "No credentials stored.\n\n"
                "Usage:\n"
                "  /creds add <site> <username> <password> [login_url]\n"
                "  /creds list\n"
                "  /creds delete <site>",
            )
            return
        await safe_send(
            update.effective_message.reply_text,
            "Stored sites:\n" + "\n".join(f"• {s}" for s in sites),
        )
        return

    sub = args[0].lower()
    if sub == "list":
        sites = CredentialVault.list_sites(user_id_db)
        await safe_send(update.effective_message.reply_text,
                        "Stored: " + (", ".join(sites) or "(none)"))
        return
    if sub == "delete" and len(args) >= 2:
        ok = CredentialVault.delete(user_id_db, args[1])
        await safe_send(update.effective_message.reply_text,
                        "Deleted." if ok else "Not found.")
        return
    if sub == "add" and len(args) >= 4:
        site, username, password = args[1], args[2], args[3]
        login_url = args[4] if len(args) >= 5 else None
        try:
            CredentialVault.store(
                user_id=user_id_db, site=site,
                username=username, password=password, login_url=login_url,
            )
            try:
                await update.effective_message.delete()
            except Exception:
                pass
            await safe_send(
                update.effective_chat.send_message,
                f"🔐 Stored credentials for `{site}` (encrypted at rest).",
            )
        except Exception as exc:
            await safe_send(update.effective_message.reply_text, f"Failed: {exc}")
        return

    await safe_send(update.effective_message.reply_text, "Unknown /creds subcommand.")


async def cmd_voice_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle whether the next reply also gets a TTS voice note."""
    if not await authorize(update, context):
        return
    flags = context.application.bot_data.setdefault("flags", {})
    flags["voice_reply"] = not flags.get("voice_reply", False)
    state = "on" if flags["voice_reply"] else "off"
    await safe_send(update.effective_message.reply_text, f"Voice replies: {state}")


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    llm = context.application.bot_data["llm"]
    u = llm.usage.as_dict()
    text = (
        "**Token usage**\n"
        f"calls: `{u['calls']}`\n"
        f"input: `{u['input_tokens']}`\n"
        f"output: `{u['output_tokens']}`\n"
        f"cache write: `{u['cache_creation_tokens']}`\n"
        f"cache read: `{u['cache_read_tokens']}`\n"
        f"est. cost: `${u['cost_usd']:.4f}`"
    )
    await safe_send(update.effective_message.reply_text, text)


async def cmd_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    if not context.args:
        await safe_send(
            update.effective_message.reply_text,
            "Usage: /private <message>\nReply will route through local Ollama only.",
        )
        return
    message = " ".join(context.args)
    orchestrator = context.application.bot_data["orchestrator"]
    user_id_db = context.application.bot_data["user_id_db"]
    response = await orchestrator.process_message(
        user_id=user_id_db, message=message, private=True,
    )
    await safe_send(update.effective_message.reply_text, response.text[:4000])


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
        await safe_send(update.effective_message.reply_text, f"Skill not available: {skill_name}")
        return

    ctx = await context_builder.build(user_id_db, message)
    sr = await skill.execute(message, ctx)
    chat = update.effective_chat
    if sr.text:
        for piece in chunk(sr.text, size=3500):
            await safe_send(chat.send_message, piece)
    if sr.inline_buttons:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [InlineKeyboardButton(b["text"], callback_data=b["callback_data"]) for b in row]
            for row in sr.inline_buttons
        ]
        await chat.send_message("Choose:", reply_markup=InlineKeyboardMarkup(keyboard))
    for f in sr.files or []:
        p = Path(f["path"])
        if p.exists():
            with p.open("rb") as fh:
                await chat.send_document(document=fh, filename=p.name)
