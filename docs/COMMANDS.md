# OMNIME — Command reference

OMNIME's primary interface is **natural language**. Slash commands exist only for two reasons:

1. **Rich-UI dashboards** — list views with inline buttons that the agentic loop won't render the same way (`/inbox`, `/files`, `/reminders`, `/scheduled_emails`, `/projects`, `/me`).
2. **Control / admin** — operations that should NEVER pass through the LLM (`/reset`, `/model`, `/backup`, `/forget`, `/creds`, `/voice`).

Everything else (drafting emails, generating CVs, fetching URLs, scheduling events, browsing the web, fixing bugs in the bot's own code, creating new GitHub repos…) just works in natural language. The agentic loop has the right primitives.

---

## Slash commands

### Dashboards / rich UI

| Command | What it does |
|---|---|
| `/me` | Living profile + active projects + top skills + recent jobs |
| `/projects` | List of structured projects. `/projects <name>` for the full record (description, role, tech, dates, key achievements, raw details). |
| `/inbox` | Triaged list of unread Gmail (🔴 action / 🟡 personal / 📰 newsletter / • other) with per-message Read / Reply / Archive buttons |
| `/scheduled_emails` | Pending scheduled sends with cancel buttons |
| `/reminders` | Pending reminders with cancel buttons |
| `/files [category]` | Uploaded files grouped by category (📑 contract / 🧾 invoice / 🪪 cv / 🖼 image / 📸 screenshot / 🧑‍🏫 whiteboard / 📝 note / 📂 other). `/files <substring>` filters by name/tag/summary. |
| `/skills` | Lists every capability the bot exposes |
| `/tools [Nh]` | 24h dashboard of tool calls — total, failures, breakdown per primitive, recent errors. `/tools 6h` for a 6-hour window. |
| `/usage` | Token usage + estimated Anthropic cost (input, output, cache reads/writes) |
| `/settings` | Current provider, briefing time, integrations on/off |

### Control

| Command | What it does |
|---|---|
| `/start` | Greeting + quick pointers |
| `/reset` | Forget the current conversation thread (start a fresh agentic session). Auto-resets after 6h idle anyway. |
| `/model [auto\|haiku\|sonnet\|opus]` | Switch the agentic-loop driver. Default `auto` runs a heuristic per message; the others force a tier. |
| `/private <message>` | Route a single message through local Ollama (never touches Anthropic). Requires Ollama configured. |
| `/voice` | Toggle TTS voice replies on/off (requires `OPENAI_API_KEY`) |

### Admin

| Command | What it does |
|---|---|
| `/search <query>` | Semantic search across `knowledge`, `conversations`, `documents` |
| `/forget <type> <name>` | Delete a structured entity with audit log entry. Types: `project`, `contact`, `skill`, `idea`, `goal`, `memory` |
| `/export` | Dump full profile JSON, sent as file |
| `/backup` | Trigger immediate backup tar.gz + off-site upload (Drive/S3/SCP/rclone) |
| `/creds <get\|set\|list> [args]` | Encrypted credential vault — site logins for the browser agent |

---

## Capabilities exposed in natural language

Everything below works just by typing. The agentic loop picks the right primitives.

### 📧 Email (Gmail)

> "mira mi inbox" / "qué hay sin leer importante?"  
> "lee el de Anthropic" · "abre el #3" · "muéstrame el correo de mi casero"  
> "redacta un mail a marc@x.com diciendo que llego tarde"  
> "responde al de Anthropic con que tengo la factura guardada"  
> "envíalo" → mail programado 10 min con botón cancelar (override `envíalo ya` / `delay_minutes=0`)  
> "busca correos de Renfe del mes pasado"

Primitives: `gmail_list / gmail_read / gmail_search / gmail_send / gmail_archive / gmail_mark_read / gmail_cancel_send`.

### 📅 Calendar (Google)

> "qué tengo esta semana" · "estoy libre el martes a las 4?"  
> "agéndame una llamada con Marc el jueves a las 10"  
> "cancela la reunión de mañana" (vía Gmail si la invite vino por email)

Primitives: `calendar_list / calendar_create / calendar_check_availability`.

### 🧠 Memory

> "qué decidí sobre el piso?" · "lo que te conté sobre Atlas"  
> "guarda que mañana voy a Madrid en avión"  
> "recuérdame en 3 días que llame al banco"  
> "quiénes son mis recordatorios pendientes?" → `/reminders`

Primitives: `memory_search / memory_save / memory_recall_profile / memory_recent_messages / memory_remind / memory_list_reminders / memory_cancel_reminder`.

Plus background entity extractor: every non-trivial message you send is parsed in background and structured facts (projects, contacts, decisions, ideas, jobs) are persisted automatically.

### 📂 Files

> Sube un PDF, foto, doc → se descarga, transcribe (OCR para fotos), clasifica, indexa, y ejecuta el extractor.  
> "qué decía el contrato del piso sobre la fianza?" → busca en el corpus  
> "lista mis facturas de mayo"  
> "borra el cv duplicado"  
> "mándame el cv aquí" → te llega como adjunto  
> "sube el cv a drive"

Primitives: `files_list / files_search / files_get / files_delete / chat_send_file / drive_upload / drive_list / drive_find / drive_create_folder / drive_move / drive_delete / drive_share_link`.

### 🌐 Web

> Pegar URL en chat → leído, resumido, indexado  
> "investiga las small language models 2025"  
> "lee guidobiosca.com y guárdame lo relevante"

Primitives: `web_fetch / web_search`.

### 🌐 Browser (vision-driven Chromium)

> "busca tren Barcelona-Zaragoza mañana 9:00 en renfe"  
> "abre LinkedIn y aplica al puesto de ML Engineer en Glovo"

Primitive: `browser_agent` (slash equivalent removed; pasalo en lenguaje natural).

### 📓 Notion

> "qué tengo en Notion sobre el TFG?"  
> "crea una página en Notion bajo X con el resumen del paper"

Primitives: `notion_search / notion_read_page / notion_create_note`.  
**Setup:** `NOTION_TOKEN` en `.env` + share pages with the integration.

### 🐙 GitHub (multi-repo + self-edit)

> "lista mis repos" · "issues abiertos en omnime" · "crea un issue en trading-bot sobre Y"  
> "lee el archivo X de mi repo Y"  
> "crea un repo privado nuevo llamado health-tracker"

Primitives: `github_list_repos / github_read_file / github_search_code / github_list_issues / github_list_pulls / github_create_issue / github_comment_issue / github_create_repo`.

### 🤖 Code work — Claude Code is the default

The bot uses **your Claude Pro/Max subscription** to do real programming work. Free within plan limits.

> "arregla el bug en /files cuando hay >50 archivos"  
> "añade un comando /weather con Open-Meteo"  
> "refactoriza browser_agent.py, mantén comportamiento, corre tests"  
> "explica cómo funciona el flow de auth de Gmail" → análisis sin commit  
> "crea un proyecto nuevo `health-tracker`: FastAPI + Postgres + webhook Strava"

**Default behavior:** direct push to `main`. Auto-deploy ships the change. Pass *"con PR para revisar"* / *"open a PR"* to switch to PR mode.

Primitives: `claude_code(prompt, repo='self|owner/name', via_pr?, ...)` · `claude_code_new_project(name, prompt, ...)`.  
Lookup helpers (trivial single-line): `bot_read_source / bot_grep_source / github_read_file / github_search_code`.

**Setup:** see `docs/SETUP.md` (or AGENTS.md) for the one-time `claude login` inside the container.

### 🐍 Code execution

> "cuánto es 15% de 847" · "convierte 230£ a euros con tipo de hoy"  
> "analiza este CSV que te pasé"

Primitive: `exec_python(code, timeout?)` — sandboxed subprocess (10s default, 30s max, 150MB cap).

### 🛰️ Proactive

The bot can ping you on its own (every 30 min) when something deserves your attention: urgent unread email, calendar event in the next hour, project without recent activity, lingering idea worth revisiting. Bias is toward silence.

Toggle via `PROACTIVE_ENABLED` in `.env`.
