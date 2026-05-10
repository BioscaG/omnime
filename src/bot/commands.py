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
        "Just tell me what you need, in any language. I can read your "
        "email, manage your calendar, search the web, generate documents, "
        "remember things you tell me, browse the web for you, and more — "
        "no slash commands required.\n\n"
        "Quick views: /me /projects /inbox /files /reminders /scheduled_emails\n"
        "Control: /reset (start fresh thread) · /model (switch tier) · /tools (usage dashboard)"
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
    """List all projects, or `/projects <name>` for full detail of one."""
    if not await authorize(update, context):
        return
    memory = context.application.bot_data["memory"]
    user_id_db = context.application.bot_data["user_id_db"]
    profile = memory.get_user_profile(user_id_db)
    projects = profile.get("projects") or []
    if not projects:
        await safe_send(update.effective_message.reply_text, "No projects stored yet.")
        return

    query = " ".join(context.args or []).strip().lower()
    if query:
        # Detail view: find a project whose name fuzzily matches the query.
        match = next(
            (p for p in projects if query in (p.get("name") or "").lower()),
            None,
        )
        if match is None:
            await safe_send(
                update.effective_message.reply_text,
                f"No project matched **{query}**. Try `/projects` for the full list.",
            )
            return
        lines = [f"**{match['name']}** ({match.get('status') or 'unknown'})"]
        if match.get("description"):
            lines.append(f"\n{match['description']}")
        if match.get("role"):
            lines.append(f"\n**Role:** {match['role']}")
        if match.get("technologies"):
            lines.append(f"**Tech:** {', '.join(match['technologies'])}")
        if match.get("start_date") or match.get("end_date"):
            lines.append(
                f"**Dates:** {match.get('start_date') or '?'} → {match.get('end_date') or 'ongoing'}"
            )
        if match.get("key_achievements"):
            lines.append("\n**Key achievements:**")
            for a in match["key_achievements"]:
                lines.append(f"  - {a}")
        if match.get("details"):
            lines.append(f"\n**Details:**\n{match['details']}")
        await safe_send(update.effective_message.reply_text, "\n".join(lines))
        return

    # List view: name + short description, with hint to drill in.
    lines = []
    for p in projects:
        tech = ", ".join(p.get("technologies") or []) or "—"
        lines.append(
            f"• **{p['name']}** ({p.get('status')}) — {p.get('description') or ''}\n  tech: {tech}"
        )
    lines.append("\n_Tip: `/projects <name>` for full detail._")
    await safe_send(update.effective_message.reply_text, "\n\n".join(lines))


async def cmd_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List + summarise unread Gmail messages with rich UI buttons."""
    text = update.effective_message.text or "/inbox"
    await _run_skill(update, context, "email_inbox", text)


async def cmd_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List uploaded files, optionally filtered by category or query."""
    if not await authorize(update, context):
        return
    user_id_db = context.application.bot_data["user_id_db"]
    query = " ".join(context.args or []).strip()

    from sqlalchemy import select
    from src.memory import models as mm
    from src.memory.db import session_scope
    from src.utils.formatters import to_telegram_html
    from telegram.constants import ParseMode

    with session_scope() as s:
        rows = s.execute(
            select(mm.FileRecord)
            .where(mm.FileRecord.user_id == user_id_db)
            .order_by(mm.FileRecord.created_at.desc())
            .limit(50)
        ).scalars().all()
        items = [
            {
                "id": r.id,
                "filename": r.filename,
                "category": (r.extra_metadata or {}).get("category") or "other",
                "title": (r.extra_metadata or {}).get("title"),
                "summary": (r.summary or "")[:160],
                "tags": r.tags or [],
                "uploaded": r.created_at.strftime("%d %b") if r.created_at else "?",
            }
            for r in rows
        ]

    # Category filter or substring search.
    if query:
        ql = query.lower()
        items = [
            it for it in items
            if ql in it["category"].lower()
            or ql in (it["filename"] or "").lower()
            or ql in (it["title"] or "").lower()
            or ql in (it["summary"] or "").lower()
            or any(ql in t.lower() for t in it["tags"])
        ]

    if not items:
        await safe_send(update.effective_message.reply_text, "No saved files matched.")
        return

    # Group by category.
    by_cat: dict[str, list] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)

    icon = {
        "contract": "📑", "invoice": "🧾", "receipt": "🧾", "cv": "🪪",
        "paper": "📄", "image": "🖼", "screenshot": "📸",
        "whiteboard": "🧑‍🏫", "note": "📝", "other": "📂",
    }

    lines = [f"**📂 {len(items)} files** ({len(by_cat)} categor{'ies' if len(by_cat) != 1 else 'y'})"]
    for cat, group in by_cat.items():
        lines.append(f"\n{icon.get(cat, '📂')} **{cat}** — {len(group)}")
        for it in group[:8]:
            title = it["title"] or it["filename"] or f"#{it['id']}"
            lines.append(f"  · _{it['uploaded']}_ — **{title}** _#{it['id']}_")
            if it["summary"]:
                lines.append(f"     {it['summary'][:140]}")

    lines.append("\n_Tip: '/files <category>' to filter; the agent can search content with files_search._")
    await update.effective_message.reply_text(
        to_telegram_html("\n".join(lines)),
        parse_mode=ParseMode.HTML,
    )


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List pending reminders with cancel buttons."""
    if not await authorize(update, context):
        return
    user_id_db = context.application.bot_data["user_id_db"]
    from sqlalchemy import select
    from src.memory import models as mm
    from src.memory.db import session_scope
    from src.utils.formatters import to_telegram_html
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode

    with session_scope() as s:
        rows = s.execute(
            select(mm.Reminder)
            .where(mm.Reminder.user_id == user_id_db)
            .where(mm.Reminder.delivered_at.is_(None))
            .order_by(mm.Reminder.due_at)
            .limit(30)
        ).scalars().all()
        items = [
            {"id": r.id, "content": r.content, "due_at": r.due_at}
            for r in rows
        ]

    if not items:
        await safe_send(update.effective_message.reply_text, "No pending reminders.")
        return

    lines = [f"**⏰ {len(items)} pending reminder(s):**\n"]
    keyboard: list[list[InlineKeyboardButton]] = []
    for it in items:
        due = it["due_at"].strftime("%d %b %H:%M") if it["due_at"] else "?"
        lines.append(f"• _{due}_ — {it['content'][:120]}")
        keyboard.append([InlineKeyboardButton(
            f"❌ Cancel #{it['id']} · {it['content'][:30]}",
            callback_data=f"reminder:cancel:{it['id']}",
        )])
    await update.effective_message.reply_text(
        to_telegram_html("\n".join(lines)),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch the agentic-loop driver model.

    Usage:
      /model           — show current
      /model auto      — heuristic picks per message (DEFAULT)
      /model haiku     — Haiku 4.5  (cheap, simple intents)
      /model sonnet    — Sonnet 4.6 (balanced)
      /model opus      — Opus 4.7   (strongest, ~5× Sonnet)
    """
    if not await authorize(update, context):
        return
    from src.brain.runtime_config import (
        AUTO,
        TIER_LABELS,
        agentic_model_tier,
        set_agentic_model_tier,
    )

    arg = " ".join(context.args or []).strip().lower()
    aliases = {
        "auto": AUTO,
        "automatic": AUTO,
        "auto-pick": AUTO,
        "heuristic": AUTO,
        "default": AUTO,
        "haiku": "tiny",
        "tiny": "tiny",
        "cheap": "tiny",
        "sonnet": "fast",
        "fast": "fast",
        "balanced": "fast",
        "opus": "powerful",
        "powerful": "powerful",
        "strong": "powerful",
    }
    if not arg:
        current = agentic_model_tier()
        if current == AUTO:
            mode_line = "**Mode:** `auto` — heuristic picks per message"
        else:
            mode_line = f"**Mode:** manual override `{current}` — {TIER_LABELS[current]}"
        lines = [
            mode_line + "\n",
            "**Switch with:**",
            "- `/model auto` — back to heuristic (recommended)",
            "- `/model haiku` — force Haiku (cheap)",
            "- `/model sonnet` — force Sonnet",
            "- `/model opus` — force Opus (top quality)",
            "\n_Heuristic:_ simple messages → Haiku · compose / writes → Sonnet · self-edit → Opus.",
        ]
        await safe_send(update.effective_message.reply_text, "\n".join(lines))
        return
    target = aliases.get(arg)
    if target is None:
        await safe_send(
            update.effective_message.reply_text,
            f"Unknown: `{arg}`. Use: auto / haiku / sonnet / opus.",
        )
        return
    set_agentic_model_tier(target)
    if target == AUTO:
        msg = "✅ Mode `auto` — heuristic picks per message from now on."
    else:
        msg = (
            f"✅ Override `{target}` active — {TIER_LABELS[target]}.\n"
            "_Back to heuristic anytime with `/model auto`._"
        )
    await safe_send(update.effective_message.reply_text, msg)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forget the current conversation thread (start a fresh agentic session)."""
    if not await authorize(update, context):
        return
    user_id_db = context.application.bot_data["user_id_db"]
    from src.brain.conversation_state import get_conversation_store

    get_conversation_store().reset(user_id_db)
    await safe_send(
        update.effective_message.reply_text,
        "🧹 Conversation reset. Next message starts a fresh thread.",
    )


async def cmd_tools_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent tool-call usage and any failures."""
    if not await authorize(update, context):
        return
    user_id_db = context.application.bot_data["user_id_db"]
    args_text = " ".join(context.args or []).strip().lower()
    hours = 24
    if args_text.endswith("h") and args_text[:-1].isdigit():
        hours = int(args_text[:-1])
    elif args_text.isdigit():
        hours = int(args_text)
    from src.memory.observability import recent_failures, tool_usage_summary
    from src.utils.formatters import to_telegram_html
    from telegram.constants import ParseMode

    summary = tool_usage_summary(user_id_db, hours=hours)
    failures = recent_failures(user_id_db, n=5)

    lines = [f"**🛠 Tool usage — last {summary['hours']}h**",
             f"Total calls: **{summary['total_calls']}** · Failures: **{summary['failures']}**\n"]
    if not summary["breakdown"]:
        lines.append("_No tool calls in this window._")
    else:
        lines.append("**Breakdown:**")
        for row in summary["breakdown"][:15]:
            avg = f"{row['avg_ms']}ms" if row.get("avg_ms") else "—"
            lines.append(f"- `{row['tool']}` · {row['calls']} calls · avg {avg}")
    if failures:
        lines.append("\n**Recent failures:**")
        for f in failures:
            lines.append(f"- `{f['tool']}` _{f['when']}_: {(f['error'] or '')[:120]}")
    await update.effective_message.reply_text(
        to_telegram_html("\n".join(lines)),
        parse_mode=ParseMode.HTML,
    )


async def cmd_scheduled_emails(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List emails scheduled to send shortly, with cancel buttons."""
    if not await authorize(update, context):
        return
    user_id_db = context.application.bot_data["user_id_db"]
    from src.skills.email_state import list_scheduled
    import time as _time

    pending = list_scheduled(user_id_db)
    if not pending:
        await safe_send(update.effective_message.reply_text, "No scheduled emails.")
        return
    lines = ["**⏰ Scheduled emails:**"]
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard: list[list[InlineKeyboardButton]] = []
    for rec in pending:
        eta = max(0, int(rec.deadline - _time.time()))
        mins = eta // 60
        secs = eta % 60
        lines.append(
            f"- → **{rec.draft.get('to')}** · *{rec.draft.get('subject')}* "
            f"_(in {mins}m {secs}s)_"
        )
        keyboard.append([InlineKeyboardButton(
            f"❌ Cancel → {rec.draft.get('to')}",
            callback_data=f"email:scheduled_cancel:{rec.id}",
        )])
    from src.utils.formatters import to_telegram_html
    from telegram.constants import ParseMode

    await update.effective_message.reply_text(
        to_telegram_html("\n".join(lines)),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await authorize(update, context):
        return
    registry = context.application.bot_data["skill_registry"]
    skills = registry.list_skills()
    lines = ["**Capabilities:**"]
    for s in skills:
        lines.append(f"• `{s.name}` — {s.description}")
    await safe_send(update.effective_message.reply_text, "\n".join(lines))


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
