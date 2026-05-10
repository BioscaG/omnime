# OMNIME — Command reference

OMNIME accepts **natural conversation** for ~90% of use. Slash commands are
shortcuts that skip the intent classifier (cheaper + faster) when you already
know what you want.

## 🎮 Core

| Command | What it does |
|---|---|
| `/start` | Greeting, quick orientation |
| `/me` | Show everything stored about you (living profile + projects + skills + jobs + ...) |
| `/skills` | List every capability the registry knows about |
| `/settings` | Current LLM provider, scheduled times, integrations on/off |
| `/usage` | Token usage + estimated cost (input, output, cache reads/writes) |

## 🧠 Memory

| Command | What it does |
|---|---|
| `/search <query>` | Semantic search across `knowledge`, `conversations`, `documents` |
| `/projects` | List structured projects with tech + status |
| `/timeline YYYY-MM-DD` | Reconstruct what was true at that date (jobs, projects, goals) |
| `/forget <type> <name>` | Delete a structured entity with audit log entry. Types: `project`, `contact`, `skill`, `idea`, `goal`, `memory` |
| `/export` | Dump full profile JSON, sent as file |
| `/backup` | Trigger immediate backup tar.gz + off-site upload |

## 📄 Document generation

| Command | What it does |
|---|---|
| `/cv` | Generic CV (Markdown + DOCX + PDF) |
| `/cv_for <job description>` | CV adapted to a specific job posting |
| `/cv_variants <job description>` | Two style variants; pick winner with inline button to bias future runs |
| `/email <objective>` | Draft an email; arrives with [Send] [Edit] [Cancel] buttons |
| `/gap <job description>` | Skill gap analysis: matches, gaps, quick wins, longer plays |

## 💼 Career engine

| Command | What it does |
|---|---|
| `/onboard` | 13-question guided interview that fills the whole profile |
| `/prep <role/company>` | STAR-format interview prep using your stored evidence |
| `/jobs` | List job opportunity pipeline |
| `/jobs Company \| Role \| status` | Add or update an opportunity (statuses: discovered/applied/interviewing/offer/rejected/withdrawn) |

## 🎯 Coaching & life

| Command | What it does |
|---|---|
| `/goal <description>` | Track a new goal with streak detection from natural conversation |
| `/journal` | Adaptive journaling prompt; reply with `/journal <entry>` to log + score sentiment |
| `/review` | Run weekly review (wins, stuck, next-week goals, reflection) |
| `/decisions` | List past decisions with status + outcome |
| `/decide <situation>` | Recall similar past decisions and reason about the new one |
| `/books` | Reading list grouped by status (reading/finished/wishlist/abandoned) |
| `/briefing` | Today's briefing: emails, calendar, pending tasks |

## 🤖 Advanced

| Command | What it does |
|---|---|
| `/plan <goal>` | Agentic multi-step planner — Claude decomposes the goal and runs skills in sequence |
| `/agent <goal>` | Alias for `/plan` |
| `/private <message>` | Route a single message through local Ollama (never touches Anthropic). Requires Ollama configured |
| `/voice` | Toggle TTS voice replies on/off (requires `OPENAI_API_KEY` for the OpenAI TTS endpoint) |
| `/graph [filter]` | Render a Mermaid knowledge graph of your projects + contacts + skills + tech |
| `/evolve <new capability>` | Generate a new skill: AST allowlist → sandbox smoke test → LLM code review → PR on GitHub |

## 📬 Email & web

| Command | What it does |
|---|---|
| `/inbox` (or `mira mi email`) | List + summarise unread Gmail messages. Requires `GMAIL_*` env vars |
| `/fetch <url>` | Read a public URL, summarise + index relevant facts into memory |
| `/browse <goal>` | Vision-driven Chromium browser drives a real session toward the goal |

> **Tip:** since the routing LLM now sees the live capability catalog, you
> *don't* need to remember any of these. "Mira mis correos sin leer" or
> "abre LinkedIn y aplica al puesto X" will route to the right skill.

## 🎙 Non-text inputs

| Input | What happens |
|---|---|
| Voice note | Whisper transcribes → routed as a normal text message |
| PDF/DOCX/TXT/MD/CSV | Text extracted → classified → summarised → chunked + indexed semantically → if it's clearly about you, entities are auto-extracted into structured tables |
| Image | Claude describes it → indexed in `documents` |
| Forwarded message | Marked with origin and processed normally |
| Inline `@OmnimeG_bot <topic>` from any chat | Returns Email draft / LinkedIn post / one-pager directly into that chat |

## ⚙️ Inline-button callbacks

These appear automatically when relevant:

- `email:send` / `email:edit` / `email:cancel` — after `/email`
- `cv_pick:<id>` — after `/cv_variants`
- `evolve:approve` / `evolve:reject` — after `/evolve`
- `review:keep` / `review:edit` — after `/review`
