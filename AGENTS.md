# OMNIME — Map for Agents

**Read this first** before self-editing or onboarding. Indexes the codebase by intent ("I want to add X — where do I touch?") and lists the project's invariants.

---

## What OMNIME is

A Telegram-based personal AI assistant. Single user, single VPS, single Postgres + ChromaDB. Talks via natural language, executes via primitive tools, persists everything user-relevant to long-term memory.

Stack: Python 3.12 · Anthropic API (Claude Sonnet/Haiku/Opus) · python-telegram-bot · SQLAlchemy + Alembic · Postgres · ChromaDB · Playwright · Docker compose.

---

## Core architecture (read in order)

```
Telegram message
   │
   ▼
src/bot/handlers.py · handle_text          ← Telegram → orchestrator
   │
   ▼
src/brain/orchestrator.py · process_message
   │
   ├── Trivial chat ("hola")  → _handle_chat
   ├── Slash command ("/cv") → _handle_task → skill direct
   ├── EVOLVE keyword        → _handle_evolve
   └── Everything else        → _run_agentic_loop  ← THE LOOP
   │
   ▼
_run_agentic_loop
   │
   ├── ConversationStore (in-memory, 6h TTL, 40 msgs)
   ├── Calls llm.agentic_step(messages, tools, system)
   ├── For each tool_use → look up in primitives_by_name → run → tool_result
   ├── Up to AGENTIC_MAX_STEPS (8) iterations
   ├── Forced final-answer turn if loop ends without text
   └── Returns Response (text + inline_buttons + files + metadata)
   │
   ▼
src/bot/handlers.py · _send_response → Telegram
```

The agent's "knowledge" of capabilities = the catalog from `src/tools/__init__.py`. Add a primitive there, it appears in the loop's tool list automatically.

---

## Tool primitives — the canonical extension point

**Every** new agent capability is a `Tool` in `src/tools/`. To add one:

1. Pick or create a file under `src/tools/` (e.g. `src/tools/calendar_tools.py`).
2. Define an async function `_do_x(args: dict, context: Context) -> str`. Return JSON-as-string.
3. Wrap it in a `Tool(name, description, input_schema, run)`.
4. Add a `build_xxx_tools()` function returning the list (often gated on `settings.xxx_token` being set).
5. Register in `src/tools/__init__.py` `collect_default_tools()` — append to the `tools` list.
6. **No need to touch the orchestrator.** It picks up the new tool through the registry on next call.

Existing primitive groups (and their build functions):

| Group | File | Build fn | Gated on |
|---|---|---|---|
| Email (Gmail) | `src/tools/email_tools.py` | `build_email_tools()` | `GMAIL_*` |
| Calendar | `src/tools/calendar_tools.py` | `build_calendar_tools()` | `GCAL_*` |
| Memory | `src/tools/memory_tools.py` | `build_memory_tools()` | always on |
| Files | `src/tools/files_tools.py` | `build_files_tools()` | always on |
| Drive | `src/tools/drive_tools.py` | `build_drive_tools()` | `GDRIVE_*` |
| Chat (send file) | `src/tools/chat_tools.py` | `build_chat_tools()` | always on |
| Web | `src/tools/web_tools.py` | `build_web_tools()` | always on |
| Notion | `src/tools/notion_tools.py` | `build_notion_tools()` | `NOTION_TOKEN` |
| GitHub | `src/tools/github_tools.py` | `build_github_tools()` | `GITHUB_TOKEN` |
| Self-edit | `src/tools/self_tools.py` | `build_self_tools()` | always on |

When the integration is gated and missing creds, the build fn returns `[]` so the catalog hides the tools — agent never promises a capability it doesn't have.

---

## Skills (legacy — slash commands only)

`src/skills/*` predates the primitives layer. Each skill subclasses `BaseSkill` and is invoked **only by slash commands** (`/cv`, `/inbox`, `/journal`, …). The agentic loop does NOT call skills directly anymore — primitives replaced them.

Don't add new skills unless you specifically want a slash-command shortcut. Add a primitive instead.

`src/skills/registry.py` registers the skill set at boot.

---

## Memory layers — where stuff lives

| Layer | Backed by | What it holds | TTL |
|---|---|---|---|
| Conversation working memory | `src/brain/conversation_state.py` (in-mem) | Current agentic loop thread | 6h |
| Structured memory (entities) | Postgres tables in `src/memory/models.py` | projects, contacts, decisions, ideas, jobs, books, files, reminders, etc. | forever |
| Semantic memory (vector) | ChromaDB (`src/memory/semantic.py`) | `knowledge`, `conversations`, `documents` collections | forever |
| Files on disk | `data/uploads/` (bind mount) | PDFs, photos, voice | forever |
| Backups | `data/backups/` + Drive `OMNIME/Backups/` | tar.gz daily snapshots + encrypted .env (opt-in) | 14d local, ∞ Drive |

**Adding a new entity type:** model in `models.py` → migration in `alembic/versions/` → CRUD in `structured.py` → entity-extraction prompt in `extractor.py` → update relevant primitive (e.g. `memory_search`) → maybe `/forget` support.

---

## System prompts — where the agent's personality lives

- `prompts/system_base.yaml` — base identity template
- `prompts/personality.yaml` — communication tone
- `prompts/skill_prompts/*.yaml` — per-skill prompts

Runtime extras (capabilities catalog, time, learned preferences, anti-hallucination rules, preview-before-write) are appended in `Orchestrator._run_agentic_loop`'s `extra=...` arg to `build_system_prompt`.

---

## Routing — common tasks → file

| Task | Where |
|---|---|
| Add a slash command | `src/bot/commands.py` (function) + `src/bot/app.py` (CommandHandler registration) + maybe regex in `_FASTPATH_PATTERNS` of orchestrator |
| Add a primitive | `src/tools/<group>.py` + register in `src/tools/__init__.py` |
| Add inline-button callback | `src/bot/callbacks.py` — extend dispatcher |
| Change Telegram rendering | `src/utils/formatters.py` — `to_telegram_html` |
| Add a scheduled job | `src/bot/app.py` — `job_queue.run_repeating(...)` near the others |
| Change agentic loop behavior | `src/brain/orchestrator.py` — `_run_agentic_loop` |
| Tweak entity extraction | `src/memory/extractor.py` — `EXTRACTION_PROMPT` + `Extraction` schema |
| Add a new integration (3rd-party API) | `src/integrations/<service>_client.py` (wrapper) + `src/tools/<service>_tools.py` (primitives) + config keys in `src/config.py` |

---

## Configuration — `src/config.py`

Pydantic-settings reads `.env`. Important groups:

- `ANTHROPIC_API_KEY` (required)
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_USER_ID` (required, single-user bot)
- `DB_*` — Postgres connection
- `GMAIL_*`, `GCAL_*`, `GDRIVE_*` — Google OAuth (separate refresh tokens per scope group)
- `NOTION_TOKEN`, `GITHUB_TOKEN` + `GITHUB_REPO`
- `BACKUP_*` — `BACKUP_REMOTE`: `none|s3|b2|scp|rclone|gdrive`. `BACKUP_ENV_PASSPHRASE` (optional) encrypts .env into the tar.
- `PROACTIVE_*` — autonomous scanner cadence

**Editing `.env` on the server requires `docker compose up -d omnime` (not `restart`)** — restart doesn't re-read `env_file`.

---

## Observability — `/tools` command

Every primitive call is recorded to the `tool_calls` table with args + latency + ok + error. `/tools 24h` renders aggregates. Use this when:
- Verifying the agent actually called the tool it claimed
- Spotting recurring errors
- Debugging cost spikes

Code: `src/memory/observability.py` + `Orchestrator._run_agentic_loop` instruments every call.

---

## Project conventions (non-negotiable)

These are PROJECT-LEVEL preferences, learned from past mistakes. Honor them:

1. **No regex for natural language.** Date parsing, intent classification, reference resolution — let Claude do it. Tools accept structured args (ISO 8601, IDs); the model converts. *See `memory/feedback_no_regex_natural_language.md` if you have memory access.*

2. **User-facing hardcoded strings in English.** The model auto-mirrors the user's language for generated text; only the rare fallback strings are baked in, and those stay English. *See `memory/feedback_user_facing_strings_english.md`.*

3. **Preview before write.** `gmail_send`, `calendar_create`, `notion_create_note`, `github_create_issue` — show full content + ask "¿lo envío?" → wait for explicit confirmation. After firing, recap in 1 line.

4. **Anti-hallucination.** Action-claim verbs ("Enviado", "Sent", "Programado", "Saved", "Created") MUST be backed by an actual tool call THIS turn. The orchestrator forces a final-answer turn if the model emits text without tools after an action; the conversation history shows the actual tool_use blocks, making lies hard.

5. **Full content on ingest.** After `web_fetch`, `files_get`, `gmail_read` — pass the FULL fetched text to `memory_save`, not a summary. Multiple `memory_save` calls per section are fine; the extractor dedups.

6. **Confirmation for destructive writes.** `files_delete`, `drive_delete`, `memory_cancel_reminder`, `bot_propose_change` — only on EXPLICIT user request, never as side effects.

---

## Code work playbook — `claude_code` is the default

**Anything substantial involving code (audit, deep dive, debug, refactor, fix, feature, scaffold a new project)** goes through `claude_code` / `claude_code_new_project`. They're free within the user's Pro/Max subscription and far better at multi-file navigation than the fast-lookup primitives.

- `claude_code(prompt, repo='self')` — work on omnime itself
- `claude_code(prompt, repo='owner/name')` — work on any repo the token can access
- `claude_code_new_project(name, prompt, description?, private?)` — create + scaffold a fresh repo

Read-only prompts ('explain X', 'audit Y') return the analysis as text and don't open a PR. Write prompts produce a diff → branch → commit → PR.

**Reserved for trivial lookups only:**
- `bot_read_source(path)` / `bot_grep_source(pattern)` — own repo, instant, $0.01
- `github_read_file(path, repo)` / `github_search_code(query, repo)` — other repos

Use these only when the answer fits on screen ('show me this function', 'where is X defined'). For anything bigger use claude_code.

Auth: Claude Code authenticates via the user's Pro/Max subscription if `data/claude-auth/` is bind-mounted at `/root/.claude` in the container. Falls back to ANTHROPIC_API_KEY if no subscription auth present. Setup is one-time: `claude login` on the user's Mac, `scp -r ~/.claude root@<vps>:/opt/omnime/data/claude-auth/`.

Standard flow when the user reports a bug or asks for a feature:

1. **Understand.** What's broken / what should it do.
2. **Read.** `bot_read_source` / `bot_grep_source` to grasp current state. Mention AGENTS.md if unfamiliar with the codebase.
3. **Diagnose explicitly** to the user — what you found + your proposed approach.
4. **Wait for explicit confirmation**: 'usa claude code' / 'with claude code' / 'arregla esto y abre PR' / 'haz el cambio'. Plain 'fix it' is NOT enough — confirm.
5. **`claude_code(prompt, repo)`** — pass a concrete prompt. Returns a PR URL.
6. **Tell the user the PR URL.** They review on GitHub, merge → auto-deploys.

**Auto-deploy:** push to `main` triggers `.github/workflows/deploy.yml` which SSHes to the VPS and runs `git reset --hard origin/main && docker compose up -d --build`.

**Deprecated:** `bot_propose_change`. Replaced by `claude_code`, which is iterative + multi-file + runs tests.

---

## Anti-patterns to avoid

- **Don't add regex to parse natural language.** Make the tool accept structured args.
- **Don't write user-facing strings in Spanish/Catalan.** English only for hardcoded; Claude generates the rest.
- **Don't bypass the agentic loop with hint-based skill dispatch for natural language.** Slash commands → skills; everything else → primitives loop.
- **Don't claim actions without calling tools.** Conversation history reveals the lie.
- **Don't add backwards-compat shims when changing internal APIs.** This is a single-user single-deploy bot — refactor freely.
- **Don't write multi-paragraph docstrings or planning docs.** One-line docstrings; docs live in `docs/CHANGELOG.md` and this file.
- **Don't add tests for things that already work.** Tests stay in `tests/` for the hard logic; don't bloat with trivials.

---

## Where to look when something is broken

| Symptom | Look at |
|---|---|
| Bot doesn't respond to message | `docker logs omnime-bot` — check for `handle_text: dispatching to orchestrator`. If missing, telegram-side. If yes, look for `agentic_loop_start` next. |
| Bot calls wrong tool | tool description in `src/tools/<group>.py` — make it more explicit/imperative |
| Bot hallucinates "Enviado" | check `/tools 5m` — if `gmail_send` not there, system prompt anti-hallucination needs strengthening or model needs to be Opus |
| Tool returns "X not configured" | `src/config.py` settings + `.env` on server. Don't forget `docker compose up -d` (not restart) |
| Memory not saving | `docker logs | grep background_extraction` — check the extractor isn't erroring. `process_and_store` in `src/memory/manager.py` |
| Migrations fail at boot | `alembic/versions/` ordering. Recent: 0001-0007. |
| Backups not uploading | `docker compose exec omnime python -m scripts.backup` to run manually + see error |

---

## Quick reference — directory layout

```
src/
├── bot/                  Telegram-side (handlers, commands, callbacks, streaming)
├── brain/                Orchestrator, agentic loop, conversation state, prompts, proactive scanner, pattern learner, reminders dispatcher
├── memory/               Postgres models, structured store, semantic store, manager, extractor, lifecycle, summarizer, observability
├── skills/               Legacy: slash-command skills (CV gen, browser agent, etc.) and email_state shared between email tools
├── tools/                THE PRIMITIVES — agent's full capability surface
├── integrations/         Third-party clients (Gmail, Calendar, Drive, Notion, GitHub, Browser via Playwright)
├── evolution/            Self-evolution scaffolding (sandbox, AST validator, self-coder for new skills)
├── utils/                formatters (Telegram HTML), credentials vault, rate limiter, TTS
└── config.py             Pydantic settings (single source of truth for env vars)

prompts/                  YAML system prompts (loaded by build_system_prompt)
alembic/versions/         DB migrations (0001-0007)
scripts/                  CLI tools: backup, restore, decrypt_env, init_db, setup_oauth
tests/                    pytest — focus on memory + routing + skills
data/                     uploads, backups, exports (bind-mounted; survive docker volume ops)
docs/                     ARCHITECTURE / CHANGELOG / COMMANDS / COST / RECOVERY
.github/workflows/        CI tests + auto-deploy to VPS
```

---

## How to add a new third-party integration (template)

Use this when wiring a new external API (e.g. Spotify, Linear, Strava):

1. Add config keys to `src/config.py` — token, refresh, etc.
2. Create `src/integrations/<service>_client.py` — wrapper class with `enabled` flag.
3. Create `src/tools/<service>_tools.py` — async primitives + `build_<service>_tools()` gated on `enabled`.
4. Register in `src/tools/__init__.py · collect_default_tools()`.
5. Document in `docs/COMMANDS.md` if exposing a slash command, or just rely on natural language.
6. Add the env vars to `.env.example` (if exists) so future deploys know what to fill.

That's it. No system-prompt edit needed; the catalog auto-includes the new tools.
