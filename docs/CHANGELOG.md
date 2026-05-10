# CHANGELOG

OMNIME no usa versionado semántico todavía — cada release está marcada por su
commit. Esta es la lista cronológica de los hitos importantes desde que el
proyecto pasó de scaffolding a su estado actual.

## 2026-05-10 — observability + pattern learning + anti-hallucination

Three things glued together because they all matter for trust:

**Observability** — every primitive call gets persisted with args,
latency, success/error, and a result preview into a new ``tool_calls``
table. New `/tools` command renders a 24h dashboard (override window
with `/tools 6h`) showing total calls, failures, breakdown by tool,
and the last 5 errors. The agentic loop also emits INFO logs
(`agentic_tool_call: tool=X args=… OK 250ms result=…`) so we can spot
hallucinated confirmations vs real executions.

**Pattern learning** — new daily APScheduler job runs Haiku over the
last ~50 messages and ~200 tool calls, extracts durable preferences
(response style, cadence, tool preferences), and upserts them into
``user_preferences`` with confidence-by-evidence. Confidence rises
with repeated observation. The agentic loop's system prompt now
includes preferences with confidence ≥ 0.4 so future turns honour
them automatically.

**Anti-hallucination** — Sonnet was caught claiming "Enviado ✓" without
calling gmail_send (real conversation: the user typed "envíalo", model
faked the confirmation). Beefed up the loop's system prompt with
non-negotiable rules: action-claim words ("sent", "scheduled",
"created", "saved") must be backed by a real tool call this turn; if
the user says "send it", the next move is a gmail_send call, not a
text confirmation. Combined with the new observability we'll catch
recurrences immediately.

New tables (migration 0006): ``tool_calls`` and ``user_preferences``.
New commands: `/tools [Nh]`. New scheduler jobs: pattern_learner_job
(daily, 6h after boot).

## 2026-05-10 — Workspace from any device: Drive sync + Notion + Telegram file sends

User wanted the bot's outputs (drafts, fetched docs, generated CVs) to be
accessible from phone/desktop without going through Telegram every time,
plus the option for the bot to push attachments back into the chat.

NEW INTEGRATIONS:
- DriveClient (src/integrations/drive_client.py): wraps Google Drive v3.
  Auto-creates an 'OMNIME' folder in the user's Drive root on first use
  (or honours GDRIVE_WORKSPACE_FOLDER_ID). Same scope-omission trick as
  Gmail/Calendar to avoid invalid_scope on refresh.
- Config: GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET / GDRIVE_REFRESH_TOKEN /
  GDRIVE_WORKSPACE_FOLDER_ID.

NEW TOOLS:
- drive_upload(file_record_id|filename) — push a stored file to Drive
- drive_list(query?) — list/search OMNIME folder
- drive_share_link(id) — generate anyone-with-link URL (gated by the
  prompt: only on explicit user request)
- chat_send_file(file_record_id|filename, caption?) — bot pushes a file
  as a Telegram document attachment back to the user, e.g. when they say
  'mándame el archivo'

INFRASTRUCTURE:
- src/bot/runtime.py — process-wide singleton for the Telegram
  Application so primitives running in async tasks can reach the bot
  without the orchestrator passing it through every signature.
- chat_send_file uses this to call application.bot.send_document().

Notion was already wired (notion_search / notion_read_page /
notion_create_note from previous commits) — now combined with Drive
they form a workspace: PDFs/images go to Drive, structured notes to
Notion, and either is one Telegram message away via chat_send_file.

## 2026-05-10 — DIOS reach: calendar/notion/github primitives + proactive scanner + longer loop

The bot now spans the full life: email + memory + web + **calendar +
notion + github**, plus an autonomous scanner that pings the user when
something deserves attention.

**Calendar primitives** (`src/tools/calendar_tools.py`):
- `calendar_list` — events in a window
- `calendar_create` — schedule with attendees + threading-aware invites
- `calendar_check_availability` — verify free slot before booking

**Notion primitives** (`src/tools/notion_tools.py`):
- `notion_search` — workspace search (pages + databases)
- `notion_read_page` — full plain-text body
- `notion_create_note` — child page under any parent

`NotionClient` extended with `search`, `get_page`, `get_block_children`,
`page_text`, `create_page`.

**GitHub primitives** (`src/tools/github_tools.py`):
- `github_list_issues` / `github_list_pulls` (filter by state)
- `github_create_issue`

**Proactive scanner** (`src/brain/proactive.py`): runs every 30 min,
collects signals (urgent unread mail, calendar events in next 4 hours,
projects without recent activity), asks Haiku to decide if anything is
worth interrupting the user, and pings if so. Bias toward silence; per-
signal 6-hour cooldown avoids spam. Configurable via `PROACTIVE_ENABLED`,
`PROACTIVE_INTERVAL_MINUTES`, `PROACTIVE_CHAT_ID`.

**Loop budget bumped** from 5 to 8 tool calls — with tier escalation:
short single-intent messages still cap at 5; compound requests
('plan my week', 'investiga X y guárdamelo y prepárame para la
entrevista') get the full budget. Heuristic: detects 2+ conjunctions or
plan/research/prep verbs.

Scope fix: removed the hardcoded `scopes=` from `CalendarClient`
credentials (same fix as `GmailClient`) — Google was rejecting refresh
with `invalid_scope` when the original consent had a narrower set.

## 2026-05-10 — pure-primitives architecture (DIOS mode)

The agentic loop now sees ONE flat catalog of tool primitives — no more
dual skill+tool layers. Every capability is a single tool the model can
compose freely:

- **Atomic primitives** (`src/tools/email_tools.py`, `memory_tools.py`,
  `web_tools.py`): single-purpose functions returning JSON. Includes
  Gmail CRUD, semantic memory search/save, profile recall, recent
  messages, web fetch, web search.
- **Compound primitives** (via `wrap_skill_as_tool`): the existing rich
  Skills (browser_agent, cv_generator, daily_briefing, code_generator,
  etc.) are now exposed to the loop as tools — each is a self-contained
  sub-agent the model can invoke. Email/web skills excluded; their
  atomic primitives cover the same surface.

Slash commands (`/inbox`, `/cv`, `/fetch`, …) keep working with their
existing rich UI — they bypass the loop and dispatch directly to skills
for instant, $0 responses.

Tool side-effects (inline buttons, files, scheduled-send IDs) bubble up
through `context._tool_side_effects` and the orchestrator attaches them
to the final Response. No fragile parsing of LLM output needed.

Driver system prompt rewritten to lean into primitives: "every capability
is a tool, you decide what to call and how to compose the answer".
Worked examples for compound flows (inbox + read + send + memory_save).

## 2026-05-10 — primitive tool layer (data-first, model composes the response)

The agentic loop no longer feeds **pre-cooked skill output** to Sonnet for
email-related work. New `src/tools/` layer exposes Gmail as **primitives**:
`gmail_list`, `gmail_read`, `gmail_search`, `gmail_send` (with default
10-min delayed send + cancellation), `gmail_archive`, `gmail_mark_read`,
`gmail_cancel_send`. Each returns raw JSON; the model interprets and
writes the user-facing response itself.

Why: when the user asked "tengo algún mail importante?", the prior
`email_inbox` skill dumped a pre-formatted list of all 10 unread
regardless of the question. Now the model calls `gmail_list`, looks at
the JSON, applies its own judgment, and answers naturally — listing only
relevant items, summarising, recommending.

Email *skills* (`email_inbox` / `email_read` / `email_search` /
`email_composer`) stay registered for slash commands (rich UI, buttons,
fully formatted), but are blocklisted from the agentic loop's tool set.
Non-email skills (browser_agent, cv_generator, web_fetch, …) remain in
the loop unchanged — they're single-shot rich actions that don't benefit
from primitive decomposition.

Scheduled-send cancellation gets surfaced automatically: when the model
calls `gmail_send` with a delay, the orchestrator detects the tool side
effect and appends an inline Cancel button to the final response.

## 2026-05-10 — agentic multi-tool loop + tiered routing

OMNIME now drives **multi-step compound requests** in a single message.
"Mira mi inbox y respóndele al de Anthropic" no longer requires two turns.

- **Agentic loop driver** in `Orchestrator._run_agentic_loop`: builds the
  full skill list as Anthropic tool-use tools (with rich JSON schemas),
  calls Sonnet 4.6 in a loop, executes tools, feeds results back, stops
  when the model writes a final text turn or hits the 5-step cap.
- **Tiered routing**: regex fast-path (free) → slash-command direct
  dispatch (no LLM) → Haiku classifier ($) → Sonnet agentic loop ($$).
  Average ~$0.30/day for typical use vs $1.50/day with all-Sonnet.
- **Rich `input_schema` on every skill** (BaseSkill default + per-skill
  overrides for email_*, web_fetch, browser_agent, web_researcher,
  cv_generator). The driver model can now extract typed args
  ('reply_to_id', 'url', 'job_description') instead of just choosing a
  skill name and rerouting via keyword scoring.
- **Per-skill `execute_with_args(args, context)`**: tool-use entry point.
  Email composer overrides it to accept `reply_to_id` / `reply_to_hint`
  directly.
- **`LLMClient.agentic_step()`**: low-level multi-turn helper that takes
  a full messages array (so tool_use ↔ tool_result blocks round-trip
  cleanly across turns).

## 2026-05-10 — full email assistant: read, search, reply-aware compose, scheduled send

- Inbox triage with category icons (🔴 action / 🟡 personal / 📰 promo /
  • other) and per-message inline buttons (Read / Reply / Archive).
- New `EmailReadSkill` opens any specific email by reference ('lee el de
  Anthropic', '#3') and summarises with TL;DR + key facts + suggested
  action.
- New `EmailSearchSkill` translates natural language to Gmail query
  operators ('busca correos de Renfe del mes pasado').
- `EmailComposerSkill` is now reply-aware: resolves references against
  the cached inbox, fetches the original body via Gmail, threads correctly
  (In-Reply-To / References / threadId), warns on no-reply addresses.
- **Scheduled send**: confirming a draft schedules the send 10 minutes
  out by default (cancellable from Telegram). Options: Send now / 10 min /
  1 hour. `/scheduled_emails` lists pending sends with cancel buttons.
- `GmailClient` gains `get_message_parsed()` (MIME walk + HTML strip),
  threading-correct `send()`, `archive()`, `mark_read()`, `is_noreply()`.

## 2026-05-10 — capability awareness + auto-deploy

- **Capability catalog injected into the system prompt**: the bot now knows
  exactly which skills it has and what they do, so natural-language requests
  ("mira mi email", "abre la web y saca info") route correctly without
  forcing the user to remember slash commands.
- **TASK tool now lists actual skills**: the routing classifier (Haiku) sees
  the full skill list with descriptions + examples and picks one by name —
  replaces the previous `find_best_skill` keyword scoring.
- **`EmailInboxSkill` added**: wraps `GmailClient` to list + summarise unread
  inbox. Hides itself from the catalog when Gmail OAuth isn't configured.
- **Auto-deploy via GitHub Actions** (`.github/workflows/deploy.yml`): every
  push to `main` SSHes to the VPS and runs `git reset --hard origin/main &&
  docker compose up -d --build`. Credentials live in repo secrets.

## 2026-05-10 — durabilidad + observabilidad

- **Daily off-site backups** (`scripts/backup.py`): tar.gz con `pg_dump` + JSON
  + uploads, retención configurable, push automático a S3 / R2 / B2 / SCP /
  rclone.
- **Smart document handling**: clasifica el tipo (CV, contrato, paper...),
  resume, indexa en chunks con overlap, y si el doc es sobre el usuario corre
  el extractor para añadir entidades a las tablas estructuradas.
- **Cost optimisations**:
  - Tier `tiny` → Haiku 4.5 para intent + extractor + sentiment + classify
    (10× más barato que Sonnet).
  - Trivial-chat fast-path: greetings y acknowledgements skipean el clasificador.
  - Slash commands extendidos en regex fast-path (incluye `/review`, `/code`).
- **Logging robusto**: handler forzado en root logger tras chromadb import
  (chromadb hijackeaba la config y silenciaba todos los `logger.info`).
- **Feature flags** (`ENABLE_PROMPT_CACHING`, `ENABLE_STREAMING`) para apagar
  partes nuevas si la SDK de Anthropic da problemas.
- **Auto-fallback**: si `cache_control` es rechazado por el API, reintenta sin
  caching automáticamente.
- Pin a IDs actuales de Claude: `sonnet-4-6`, `opus-4-7`, `haiku-4-5`.

## 2026-05-09 — segunda oleada de features

- **Personal capture**: nuevas tablas `books`, `decisions`, `health_events`,
  `quotes`, `job_opportunities`, `cv_variants`. El extractor las llena solo.
- **Career engine**: `/onboard` (entrevista 13 preguntas), `/prep` (STAR),
  `/jobs` (pipeline), `/cv_variants` (A/B con feedback), `/gap` (skill gap).
- **PKM**: `/graph` (Mermaid), `/books`, `/decisions` + `/decide <X>` (situational
  recall).
- **Coach**: `/journal` con sentiment scoring, `/timeline YYYY-MM-DD`,
  `birthday_reminder_job`.
- **Inteligencia conversacional**: `/plan` (agentic multi-step planner), inline
  mode (`@OmnimeG_bot <topic>` desde cualquier chat), TTS helper, `/private`
  (Ollama-only).
- **Memory lifecycle**: importance scoring, decay con half-life de 60 días,
  dedup semántico al escribir, `/forget` con audit trail.
- **Weekly review** automatizado los domingos 19:00.
- **Bidirectional Notion sync**: push de proyectos/ideas/contactos a Notion DBs,
  pull de ideas creadas en Notion.
- **Real evolution sandbox**: AST allowlist, env stripping, opcional Docker
  con `--network=none`, LLM code review pass, branch + auto-PR.
- **Anthropic prompt caching** + **tool use** en `LLMClient`.
- **Streaming a Telegram** con edits debounced.
- **Encryption at rest** para `email` y `phone` en `contacts` (Fernet).
- **Telegram parse_mode hardening**: MarkdownV2 con escape seguro y fallback.
- **Whisper non-blocking + singleton**.
- **Photo + forwarded message handlers**.

## 2026-05-09 — primera versión funcional

- Scaffolding: Docker, requirements, .env.example, LICENSE, MIT.
- Configuración centralizada con pydantic-settings.
- Modelos SQLAlchemy + migraciones Alembic (initial schema).
- Memoria: structured CRUD, ChromaDB store, extractor LLM-driven, summarizer,
  manager.
- Brain: LLM client multi-provider, prompts YAML, context builder, orchestrator.
- 6 skills iniciales: CV, email, document, web research, daily briefing,
  code generator.
- Telegram bot: handlers, commands, callbacks, middleware.
- Integraciones: Gmail, Calendar, GitHub clients (todas opcionales).
- Self-evolution v1: code generation, sandbox smoke test, deployer con
  hot-reload.
- Utils, scripts, prompts YAML, main entrypoint.
- Suite de tests con SQLite in-memory + FakeLLM.
- CI/CD workflows + issue/PR templates + CONTRIBUTING + SETUP_GUIDES.
- Rename ATLAS → OMNIME en todo el codebase.

## Tests

- 95 tests, todos verde.
- Cubren: formatters, AST validator, sandbox, lifecycle, forget audit, goals,
  orchestrator routing, streaming, LLM caching, calendar overlaps, crypto,
  Notion serialization, evolution engine, weekly review, proactive refresh,
  personal capture, career skills, PKM skills, coach skills, planner, document
  analyzer.
