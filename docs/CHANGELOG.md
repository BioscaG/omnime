# CHANGELOG

OMNIME no usa versionado semántico todavía — cada release está marcada por su
commit. Esta es la lista cronológica de los hitos importantes desde que el
proyecto pasó de scaffolding a su estado actual.

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
