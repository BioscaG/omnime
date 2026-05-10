# OMNIME architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     TELEGRAM BOT                            │
│   handlers (text/voice/photo/doc/forwarded) + commands +    │
│   callbacks + inline mode + streaming edits                 │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   ORCHESTRATOR                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 1. Trivial-chat fast-path (regex)                    │   │
│  │ 2. Slash-command fast-path (regex)                   │   │
│  │ 3. Tool-use intent classifier (Claude Haiku)         │   │
│  └──────────────────────────────────────────────────────┘   │
│                         │                                   │
│       ┌─────────┬───────┼────────┬──────────┐               │
│       ▼         ▼       ▼        ▼          ▼               │
│   STORE     QUERY    TASK     CHAT      EVOLVE              │
└───────┬─────────┬───────┬───────┬──────────┬────────────────┘
        │         │       │       │          │
        ▼         ▼       ▼       ▼          ▼
  ┌──────────┐ ┌──────┐ ┌──────────┐ ┌─────┐ ┌─────────┐
  │ Extractor│ │Search│ │ Skills   │ │Chat │ │Evolution│
  │ (Haiku)  │ │ + LLM│ │ Registry │ │+LLM │ │Engine   │
  └────┬─────┘ └───┬──┘ └────┬─────┘ └──┬──┘ └────┬────┘
       │           │         │          │         │
       ▼           ▼         ▼          ▼         ▼
┌─────────────────────────────────────────────────────────────┐
│                      MEMORY MANAGER                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │ Structured  │  │  Semantic    │  │  Lifecycle       │    │
│  │ (Postgres)  │  │  (ChromaDB)  │  │  (decay, dedup,  │    │
│  │             │  │              │  │   importance)    │    │
│  └─────────────┘  └──────────────┘  └──────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Tres capas de memoria

### 1. PostgreSQL — datos estructurados tipados

| Tabla | Qué guarda |
|---|---|
| `user_profile` | Identidad, bio, estilo de comunicación, personalidad, **living profile** |
| `projects` | Proyectos con tech, rol, fechas, logros, importancia, último uso |
| `work_experience` | Empresa, rol, fechas, logros, tech |
| `education` | Estudios, tesis, notas |
| `skills` | Habilidades con nivel + años + último uso |
| `contacts` | Personas (email/teléfono encriptados con `ENCRYPTION_KEY`) |
| `achievements` | Logros con impacto medible |
| `life_events` | Eventos personales |
| `ideas` | Brainstorm bucket con tags y estado |
| `books` | Reading list con takeaways y rating |
| `decisions` | Decisiones con racional + alternativas + outcome |
| `health_events` | Eventos médicos / wellness |
| `quotes` | Frases destacadas |
| `goals` | Objetivos con streak |
| `weekly_reviews` | Wins/stuck/goals semanales |
| `job_opportunities` | Pipeline de ofertas |
| `cv_variants` | Variantes A/B con tracking del ganador |
| `audit_log` | Quién/qué/cuándo se borró o cambió (compliance) |
| `conversations` | Cada mensaje con intent + entidades + sentiment |
| `memory_summaries` | Resúmenes periódicos auto-generados |
| `files` | PDFs/docs/imágenes con texto extraído + summary + tags + categoría |

### 2. ChromaDB — memoria semántica

Tres colecciones:
- `conversations` — mensajes del usuario indexados
- `knowledge` — hechos extraídos (proyectos, achievements, decisiones, libros, ideas, quotes, jobs)
- `documents` — texto/descripción de archivos chunked

Cada vector lleva metadata: `user_id`, `category`, `importance` (0-1), `date`.

**Lifecycle automático** (`memory/lifecycle.py`):
- Importance scoring al guardar (0.05–1.0)
- Forgetting curve nightly (half-life 60d)
- Dedup semántico al escribir (umbral 0.12)
- Prune de entradas con importancia < 0.08 marcadas como abandonadas

### 3. Living profile

Texto de ~250-400 palabras que resume todo lo que el sistema sabe del usuario.
Se regenera:
- Cada 24h (job programado)
- **Tras cualquier STORE meaningful** (cambio de trabajo, nuevo proyecto, profile update)

Va en el system prompt con `cache_control: ephemeral` → cache hits del 50-90%.

## Tres capas de modelo (cost-optimised)

| Tier | Modelo | Para qué |
|---|---|---|
| `tiny` | `claude-haiku-4-5-20251001` | Intent classification, extractor, sentiment scoring, document classifier (~$1/M input) |
| `fast` | `claude-sonnet-4-6` | Chat, query answering, streaming replies (~$3/M input) |
| `powerful` | `claude-opus-4-7` | CV generation, weekly review, code review, planning (~$15/M input) |

## Skills registrados

17 skills al arranque (`src/skills/registry.py:_register_default_skills`):

`cv_generator`, `email_composer`, `document_generator`, `web_researcher`,
`daily_briefing`, `code_generator`, `weekly_review`, `career_onboarding`,
`interview_prep`, `job_tracker`, `cv_variants`, `skill_gap`,
`knowledge_graph`, `reading_list`, `decision_log`, `journaling`,
`time_machine`, `agentic`.

Cada skill implementa el interfaz `BaseSkill` (`name`, `description`, `triggers`,
`can_handle()`, `execute()`).

## Scheduled jobs

| Job | Cuándo | Qué hace |
|---|---|---|
| `briefing_job` | `DAILY_BRIEFING_TIME` (default 08:00) | Manda briefing del día |
| `profile_refresh_job` | Cada 24h | Regenera living profile |
| `memory_maintenance_job` | Cada 24h | Decay + prune |
| `weekly_review_job` | Domingo 19:00 | Auto-genera weekly review |
| `notion_sync_job` | Cada 6h | Sync proyectos/ideas/contactos a Notion |
| `birthday_reminder_job` | 08:15 diario | Avisa si hay birthdays en 7 días |
| `backup_job` | `BACKUP_AT` (default 03:00) | Tar.gz + push off-site |

## Self-evolution

Pipeline `/evolve <capability>`:

1. **Code generation** (Opus) — escribe el módulo del skill
2. **AST allowlist** — rechaza `os`, `subprocess`, `socket`, `eval`, etc.
3. **Sandbox smoke test** — subprocess con env stripped (PYTHONPATH solo apunta a src), opcional Docker container con `--network=none --read-only`
4. **LLM code review** (Opus) — segundo modelo revisa por seguridad
5. **Approve via inline button** → escribe el fichero, hot-reload en el registry
6. **GitHub** (opcional) — commit a branch `evolve/<slug>-<timestamp>` + auto-PR

## Persistencia y backups

- **Volúmenes Docker** persisten entre restarts: `postgres_data`, `chroma_data`
- **Bind mounts** del host: `data/uploads`, `data/exports`, `data/backups`
- **Backup diario automático** a las 03:00:
  - `pg_dump` completo + JSON estructurado + `uploads/` → tar.gz
  - Retención: 14 backups locales (configurable)
  - Off-site opcional: S3, R2, B2, SCP, rclone

Ver [`RECOVERY.md`](RECOVERY.md) para procedimiento de restore.
