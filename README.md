# ATLAS — Your AI-Powered Digital Twin

> **A self-hosted personal AI assistant that lives in Telegram, remembers everything about your life, and acts on your behalf.**

ATLAS learns who you are — your projects, skills, career history, contacts, ideas, and life events — and uses that knowledge to help you: generate tailored CVs, draft emails in your voice, prepare for interviews, track your goals, and much more. It even evolves itself by writing and deploying new capabilities on demand.

---

## ✨ Features

### 🧠 Persistent Memory System
- **Structured storage** (PostgreSQL): Projects, work experience, skills, contacts, achievements, education — all organized and queryable
- **Semantic search** (ChromaDB): Tell ATLAS anything in natural language. Later, ask "what was that ML project I worked on last summer?" and it finds it — even if you use completely different words
- **Auto-extraction**: ATLAS automatically detects and stores entities from your messages — projects, people, skills, dates, achievements — without you having to organize anything
- **Progressive summaries**: Periodic auto-generated summaries keep your "living profile" up to date

### 💬 Natural Telegram Interface
- Talk to ATLAS like you'd talk to a friend — it understands context and intent
- Send voice messages, documents, images — it processes everything
- Inline buttons for confirmations before any external action
- Works on phone and desktop simultaneously

### 📄 Smart Document Generation
- **CV Generator**: Instantly create tailored CVs. Pass a job description and get a CV optimized for that specific role
- **Cover Letters**: Personalized cover letters that reference your actual experience
- **Project Briefs**: One-pagers, proposals, reports — all built from your stored knowledge
- **LinkedIn Posts**: Draft content that sounds like you

### 📧 Email Integration
- Read and search your Gmail inbox
- Draft replies that match your writing style
- Review before sending — nothing goes out without your approval

### 📅 Daily Briefing
- Automated morning summary: unread emails, today's calendar, pending tasks, reminders
- Customizable schedule and content

### 🔄 Self-Evolution
- Ask ATLAS to add new capabilities: *"Add the ability to check cryptocurrency prices"*
- It writes the code, tests it in a sandbox, shows you the changes, and deploys on approval
- Plugin-based architecture makes it easy to extend manually too

### 🔍 Web Research
- Research companies before interviews
- Look up anything and get summarized results
- Track news on topics you care about

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                           │
│               (Primary user interface)                      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                   ORCHESTRATOR (Python)                     │
│           Intent routing + Context management               │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Memory   │  │  Skills  │  │ Integra- │  │   Self-    │  │
│  │ Engine   │  │  Engine  │  │  tions   │  │ Evolution  │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │
└─────────────────────────────────────────────────────────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
┌──────────────┐ ┌──────────┐ ┌──────────────┐
│  PostgreSQL  │ │ ChromaDB │ │ External APIs│
│ (Structured) │ │(Vectors) │ │(Gmail, etc.) │
└──────────────┘ └──────────┘ └──────────────┘
```

### Design Philosophy

- **One instance = one user.** Each person deploys their own ATLAS. Your data never leaves your server.
- **Memory-first**: Every interaction attempts to extract and store useful information.
- **Confirm before acting**: External actions (sending emails, committing code) always require explicit approval.
- **Graceful degradation**: If a service fails (LLM API, ChromaDB), the bot continues with reduced capabilities.
- **Provider-agnostic**: Swap LLM providers (Anthropic, OpenAI, Ollama) via configuration.

---

## 🚀 Quick Start

### Prerequisites

- A server, VPS, or local machine with Docker installed
- A Telegram account
- An Anthropic API key (or OpenAI key, or local Ollama setup)

### 1. Create your Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the bot token — you'll need it in step 3
4. Send `/setcommands` to BotFather and paste:
   ```
   start - Initialize ATLAS
   me - Show everything ATLAS knows about you
   search - Search your memory
   projects - List your projects
   cv - Generate your CV
   cv_for - Generate CV tailored to a job posting
   email - Compose an email
   briefing - Get your daily briefing
   export - Export your data
   skills - List ATLAS capabilities
   evolve - Add a new capability
   settings - Configure ATLAS
   backup - Create a backup
   ```

### 2. Get your Telegram User ID

Message [@userinfobot](https://t.me/userinfobot) on Telegram — it will reply with your numeric user ID.

### 3. Configure environment

```bash
git clone https://github.com/yourusername/atlas.git
cd atlas
cp .env.example .env
```

Edit `.env` with your values:

```bash
# === REQUIRED ===
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_USER_ID=your_numeric_user_id
ANTHROPIC_API_KEY=your_anthropic_api_key

# === DATABASE (defaults work with Docker) ===
DB_HOST=postgres
DB_PORT=5432
DB_NAME=atlas
DB_USER=atlas
DB_PASSWORD=change_this_to_a_secure_password

# === CHROMADB (defaults work with Docker) ===
CHROMA_HOST=chromadb
CHROMA_PORT=8000
```

See [Configuration Reference](#-configuration-reference) for all options including Gmail, Calendar, and GitHub integration.

### 4. Launch

```bash
docker-compose up -d
```

### 5. Start talking

Open Telegram, find your bot, send `/start`. That's it — start telling ATLAS about yourself.

---

## 📖 Usage Guide

### Telling ATLAS about yourself

Just talk naturally. ATLAS automatically detects and stores relevant information:

```
You: "I just started a new job at Google as a Senior ML Engineer.
      We're building a recommendation system using PyTorch and
      working with the Search team led by Sarah Chen."

ATLAS: "Got it! I've recorded:
        📋 New position: Senior ML Engineer at Google
        🔧 Project: Recommendation system (PyTorch)
        👤 Contact: Sarah Chen (Search team lead, Google)
        Want to add more details?"
```

### Searching your memory

```
You: /search machine learning projects

ATLAS: "Here's what I found:
        1. Recommendation System @ Google (current) - PyTorch, started Jan 2025
        2. Fraud Detection Model @ Acme Corp (2024) - TensorFlow, 40% improvement
        3. NLP Pipeline @ University (2023) - Thesis project, BERT-based
        Want details on any of these?"
```

### Generating a CV

```
You: /cv_for Here's a job posting for Staff ML Engineer at Meta...
     [pastes job description]

ATLAS: "I've generated a CV tailored for this Staff ML Engineer role at Meta.
        I highlighted your recommendation system work at Google and the
        fraud detection project since they match the requirements.
        [📄 Download PDF] [📝 Download DOCX] [✏️ Edit]"
```

### Email drafting

```
You: "Draft an email to Sarah about postponing tomorrow's review meeting
      to next week. Keep it casual."

ATLAS: "Here's a draft:

        Subject: Quick reschedule - review meeting

        Hey Sarah,
        Would it be okay to push tomorrow's review to next week?
        [your context-aware reason]. Happy to keep the same time
        slot on [suggested day].
        Thanks!

        [✅ Send] [✏️ Edit] [❌ Cancel]"
```

---

## 🗂️ Project Structure

```
atlas/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── CONTRIBUTING.md
├── SETUP_GUIDES.md               # Detailed setup for Gmail, Calendar, etc.
│
├── alembic/                      # Database migrations
│   ├── alembic.ini
│   └── versions/
│
├── src/
│   ├── __init__.py
│   ├── main.py                   # Entry point
│   ├── config.py                 # Centralized configuration
│   │
│   ├── bot/                      # Telegram Bot
│   │   ├── __init__.py
│   │   ├── handlers.py           # Message handlers
│   │   ├── commands.py           # Command handlers (/start, /search, etc.)
│   │   ├── callbacks.py          # Inline button callbacks
│   │   └── middleware.py         # Rate limiting, logging, auth
│   │
│   ├── brain/                    # AI Engine
│   │   ├── __init__.py
│   │   ├── orchestrator.py       # Intent router + main logic
│   │   ├── llm_client.py         # LLM API client (multi-provider)
│   │   ├── prompts.py            # Dynamic system prompt builder
│   │   └── context_builder.py    # Builds context from memory
│   │
│   ├── memory/                   # Memory System
│   │   ├── __init__.py
│   │   ├── manager.py            # Memory coordinator
│   │   ├── structured.py         # PostgreSQL CRUD operations
│   │   ├── semantic.py           # ChromaDB vector search
│   │   ├── summarizer.py         # Progressive summarization
│   │   ├── extractor.py          # Entity extraction from messages
│   │   └── models.py             # SQLAlchemy models
│   │
│   ├── skills/                   # Capabilities / Actions
│   │   ├── __init__.py
│   │   ├── base.py               # Base skill interface
│   │   ├── cv_generator.py       # CV generation
│   │   ├── email_composer.py     # Email drafting
│   │   ├── document_generator.py # Document generation
│   │   ├── web_researcher.py     # Web search + summarization
│   │   ├── daily_briefing.py     # Daily summary
│   │   ├── code_generator.py     # Code generation
│   │   └── registry.py           # Dynamic skill registry
│   │
│   ├── integrations/             # External services
│   │   ├── __init__.py
│   │   ├── gmail_client.py       # Gmail API
│   │   ├── calendar_client.py    # Google Calendar API
│   │   └── github_client.py      # GitHub API
│   │
│   ├── evolution/                # Self-evolution system
│   │   ├── __init__.py
│   │   ├── self_coder.py         # Generates new skill code
│   │   ├── deployer.py           # Commit + hot-reload
│   │   └── sandbox.py            # Safe code execution
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py
│       ├── rate_limiter.py
│       └── formatters.py
│
├── tests/
│   ├── conftest.py
│   ├── test_memory.py
│   ├── test_orchestrator.py
│   ├── test_skills.py
│   ├── test_extractor.py
│   └── test_context_builder.py
│
├── scripts/
│   ├── init_db.py                # Database initialization
│   ├── backup.py                 # Data backup utility
│   └── restore.py                # Data restore utility
│
├── prompts/                      # System prompts (YAML, easy to customize)
│   ├── system_base.yaml
│   ├── personality.yaml
│   └── skill_prompts/
│       ├── cv.yaml
│       ├── email.yaml
│       └── extraction.yaml
│
└── .github/
    ├── workflows/
    │   ├── ci.yml                # Run tests on PR
    │   └── docker.yml            # Build + push Docker image
    ├── ISSUE_TEMPLATE/
    │   ├── bug_report.md
    │   └── feature_request.md
    └── PULL_REQUEST_TEMPLATE.md
```

---

## 🧩 Module Specifications

### 1. Telegram Bot (`src/bot/`)

The bot is the primary interface. Every interaction flows through here.

**Implementation details:**
- Library: `python-telegram-bot` v20+ (async native)
- Webhook mode for production, polling mode for development (auto-detected)
- Only responds to the configured `TELEGRAM_USER_ID` — all other messages are ignored silently
- Middleware layer handles logging, rate limiting, and error recovery

**Message flow:**
```
User message → Middleware (auth + logging)
             → Orchestrator (intent classification)
             → Appropriate handler/skill
             → Response with optional inline buttons
```

**Supported message types:**
- Text: Natural conversation, processed by orchestrator
- Voice: Transcribed via Whisper API, then processed as text
- Documents: PDFs, images, files — extracted and stored
- Forwarded messages: Analyzed and acted upon (e.g., forward an email to draft a reply)

---

### 2. AI Engine (`src/brain/`)

#### Orchestrator (`orchestrator.py`)

The central router. Classifies every message into an intent and routes accordingly:

| Intent | Description | Example |
|--------|-------------|---------|
| `STORE` | User is sharing information to remember | "I just got promoted to Tech Lead" |
| `QUERY` | User is asking about their own data | "What technologies did I use at Acme?" |
| `TASK` | User wants an action performed | "Generate my CV" / "Draft an email to John" |
| `CHAT` | Casual conversation | "What do you think about React vs Vue?" |
| `EVOLVE` | Request for new capability | "Add the ability to track my expenses" |

```python
class Orchestrator:
    async def process_message(self, message: str, user_id: int) -> Response:
        # 1. Classify intent
        intent = await self.classify_intent(message)

        # 2. Build relevant context from memory
        context = await self.context_builder.build(message, intent, user_id)

        # 3. Route to appropriate handler
        match intent:
            case Intent.STORE:
                return await self.memory_manager.process_and_store(message, context)
            case Intent.QUERY:
                return await self.handle_query(message, context)
            case Intent.TASK:
                skill = self.skill_registry.find_best_skill(message, context)
                return await skill.execute(message, context)
            case Intent.EVOLVE:
                return await self.evolution_engine.handle(message, context)
            case _:
                return await self.chat(message, context)
```

#### LLM Client (`llm_client.py`)

Multi-provider LLM client with automatic failover:

```python
# Provider hierarchy (configurable in .env):
# 1. Anthropic Claude (default) — best for complex reasoning and long context
# 2. OpenAI GPT-4 — fallback
# 3. Ollama (local) — offline fallback, free

# Model selection strategy:
# - Fast model (e.g., claude-sonnet) for: intent classification, simple chat, entity extraction
# - Powerful model (e.g., claude-opus) for: CV generation, document writing, complex analysis

# Features:
# - Retry with exponential backoff (3 attempts)
# - Automatic provider failover
# - Response caching for repeated queries
# - Token usage tracking for cost monitoring
# - Streaming support for long responses
```

**Configuration:**
```bash
# .env — Choose your provider
LLM_PROVIDER=anthropic              # anthropic | openai | ollama
LLM_MODEL_FAST=claude-sonnet-4-20250514
LLM_MODEL_POWERFUL=claude-opus-4-20250514

# Optional fallback
LLM_FALLBACK_PROVIDER=openai
LLM_FALLBACK_MODEL=gpt-4o
```

#### Dynamic System Prompt (`prompts.py`)

The system prompt is rebuilt for each conversation, incorporating the user's personality and context:

```yaml
# prompts/system_base.yaml
base_identity: |
  You are ATLAS, the personal AI assistant of {user_name}.
  Your goal is to be their digital twin: you know their history,
  their communication style, their projects, and you act on their
  behalf when asked.

  USER PERSONALITY:
  {extracted_personality}

  COMMUNICATION STYLE:
  {communication_style}

  CURRENT CONTEXT:
  - Active projects: {active_projects}
  - Upcoming events: {upcoming_events}
  - Pending tasks: {pending_tasks}

  RULES:
  - Never send anything externally without explicit user confirmation.
  - When storing information, confirm what you've saved.
  - If unsure about something, ask.
  - Match the user's language (auto-detect).
  - Be concise but thorough.
```

#### Context Builder (`context_builder.py`)

Assembles the optimal context window for each interaction:

```python
class ContextBuilder:
    """
    For each user message, builds the most relevant context by pulling from memory:

    1. Living profile (always included — compressed user summary)
    2. Semantic search results (top 5-10 fragments matching the message)
    3. Structured data (if a project/skill/contact is referenced, fetch it)
    4. Recent conversation history (last N messages)
    5. Conversation summary (if conversation is long)

    Context is prioritized by relevance and trimmed to fit the model's
    context window (200k tokens for Claude).
    """
```

---

### 3. Memory System (`src/memory/`)

**This is the heart of ATLAS.** The memory system has three layers that work together.

#### 3.1 Structured Storage — PostgreSQL

Full database schema with SQLAlchemy models:

```sql
-- User profile
CREATE TABLE user_profile (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    name VARCHAR(255),
    bio TEXT,                          -- Auto-generated bio
    communication_style TEXT,          -- Detected writing style
    personality_traits JSONB,          -- Extracted traits
    preferences JSONB,                -- Language, format, timezone, etc.
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Projects
CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    name VARCHAR(500) NOT NULL,
    description TEXT,
    role VARCHAR(255),                 -- User's role in this project
    technologies JSONB,                -- Technologies used
    status VARCHAR(50) DEFAULT 'active', -- active | completed | paused | abandoned
    start_date DATE,
    end_date DATE,
    key_achievements JSONB,            -- Measurable achievements
    challenges TEXT,                   -- Challenges faced
    collaborators JSONB,               -- People involved
    links JSONB,                       -- Relevant URLs
    details TEXT,                      -- Free-form detailed description
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Work experience
CREATE TABLE work_experience (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    company VARCHAR(255) NOT NULL,
    role VARCHAR(255) NOT NULL,
    description TEXT,
    start_date DATE,
    end_date DATE,                     -- NULL = current position
    achievements JSONB,
    technologies JSONB,
    location VARCHAR(255),
    remote BOOLEAN DEFAULT FALSE,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Education
CREATE TABLE education (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    institution VARCHAR(255) NOT NULL,
    degree VARCHAR(255),
    field VARCHAR(255),
    start_date DATE,
    end_date DATE,
    grade VARCHAR(50),
    achievements JSONB,
    courses JSONB,
    thesis TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Skills
CREATE TABLE skills (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100),             -- technical | soft | language | tool
    proficiency VARCHAR(50),           -- beginner | intermediate | advanced | expert
    years_experience FLOAT,
    context TEXT,                       -- Where/how this skill was acquired
    last_used DATE,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Contacts / Network
CREATE TABLE contacts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    name VARCHAR(255) NOT NULL,
    relationship VARCHAR(100),         -- colleague | friend | mentor | client | manager
    organization VARCHAR(255),
    email VARCHAR(255),
    phone VARCHAR(50),
    notes TEXT,
    last_interaction DATE,
    context TEXT,                       -- How they met
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Achievements / Milestones
CREATE TABLE achievements (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    date DATE,
    category VARCHAR(100),             -- professional | personal | academic
    project_id INTEGER REFERENCES projects(id),
    impact TEXT,                        -- Measurable impact
    evidence JSONB,                    -- Links, documents
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Life events
CREATE TABLE life_events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    date DATE,
    category VARCHAR(100),             -- travel | health | personal | milestone
    location VARCHAR(255),
    people_involved JSONB,
    lessons_learned TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Ideas / Notes
CREATE TABLE ideas (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    content TEXT NOT NULL,
    category VARCHAR(100),
    tags JSONB,
    status VARCHAR(50) DEFAULT 'raw',  -- raw | developing | implemented | discarded
    related_project_id INTEGER REFERENCES projects(id),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Conversation log
CREATE TABLE conversations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    message_text TEXT NOT NULL,
    role VARCHAR(20) NOT NULL,         -- user | assistant
    intent VARCHAR(50),
    entities_extracted JSONB,
    telegram_message_id BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Periodic summaries
CREATE TABLE memory_summaries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    summary TEXT NOT NULL,
    key_updates JSONB,
    topics JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Received files
CREATE TABLE files (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES user_profile(id),
    filename VARCHAR(500),
    file_type VARCHAR(100),
    telegram_file_id VARCHAR(255),
    extracted_text TEXT,
    summary TEXT,
    tags JSONB,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### 3.2 Semantic Memory — ChromaDB

Every piece of information is also stored as a vector embedding for natural language search:

```python
# Collections:
# - "conversations": All user messages with timestamps
# - "knowledge": Extracted facts, organized and deduplicated
# - "documents": Content from processed files

# Each entry includes metadata:
{
    "source": "conversation | document | manual",
    "date": "2025-01-15",
    "category": "project | skill | personal | work | education",
    "related_entities": ["project:atlas", "skill:python"],
    "importance": 0.8  # 0-1, auto-calculated
}
```

#### 3.3 Entity Extractor (`extractor.py`)

Automatically detects and extracts structured data from natural messages:

```python
class EntityExtractor:
    """
    Analyzes user messages and extracts storable entities.

    Input: "Today I had a meeting with the Google team and presented the
           pricing model. Sarah Chen loved it and we're moving to phase 2."

    Extracted output:
    {
        "life_event": {
            "title": "Meeting with Google - pricing model presentation",
            "date": "2025-01-15",
            "category": "professional"
        },
        "project_update": {
            "project": "Pricing Model @ Google",
            "update": "Presented to team, moving to phase 2"
        },
        "contact_update": {
            "name": "Sarah Chen",
            "last_interaction": "2025-01-15",
            "notes": "Positive reception to pricing model"
        }
    }

    Key behaviors:
    - Uses existing context to avoid duplicates (update vs create)
    - Asks the user for confirmation when uncertain
    - Assigns importance scores automatically
    - Links related entities (contact ↔ project ↔ company)
    """
```

#### 3.4 Summarizer (`summarizer.py`)

```python
class Summarizer:
    async def generate_weekly_summary(self, user_id: int) -> str:
        """Weekly digest of everything discussed + changes in memory."""

    async def update_living_profile(self, user_id: int) -> str:
        """
        Updates the 'living profile': a comprehensive document summarizing
        EVERYTHING ATLAS knows about the user. Used as part of the system
        prompt. Recalculated periodically and after significant changes.
        """

    async def generate_project_brief(self, project_id: int) -> str:
        """Generates an executive summary of a specific project."""
```

---

### 4. Skills (`src/skills/`)

Each skill is an independent module following a common interface:

```python
from abc import ABC, abstractmethod

class BaseSkill(ABC):
    name: str
    description: str
    triggers: list[str]  # Words/phrases that activate this skill

    @abstractmethod
    async def execute(self, message: str, context: dict) -> SkillResponse:
        pass

    @abstractmethod
    def can_handle(self, message: str, intent: str) -> float:
        """Returns 0-1 probability that this skill should handle the message."""
        pass
```

#### Included Skills:

**CV Generator** (`cv_generator.py`)
- Full CV from stored data
- Tailored CV optimized for a specific job posting
- Multiple output formats: PDF, DOCX, Markdown
- Automatic selection of most relevant projects/skills for the target role
- Cover letter generation

**Email Composer** (`email_composer.py`)
- Draft new emails given an objective
- Reply to forwarded emails
- Tone adaptation based on recipient (detected from contact history)
- Auto-detect language of original email
- Review flow: Draft → [Send] [Edit] [Cancel]

**Document Generator** (`document_generator.py`)
- Project one-pagers and briefs
- Technical proposals
- Progress reports
- LinkedIn/blog posts in the user's voice

**Web Researcher** (`web_researcher.py`)
- Research any topic with summarized results
- Company research for interview prep
- Technology comparisons

**Daily Briefing** (`daily_briefing.py`)
- Automated morning summary (configurable time)
- Unread emails, today's calendar, pending tasks, reminders
- Contact birthdays, relevant news

**Code Generator** (`code_generator.py`)
- Generate code snippets and scripts
- Explain code
- Debug assistance

---

### 5. Integrations (`src/integrations/`)

All integrations are optional. ATLAS works without any of them — they just add superpowers.

#### Gmail (`gmail_client.py`)
- OAuth2 authentication (setup guide in SETUP_GUIDES.md)
- Read unread emails, search by criteria
- Send emails and create drafts (only with user confirmation)
- Reply to threads
- Label and archive

#### Google Calendar (`calendar_client.py`)
- View upcoming events
- Create events
- Conflict detection
- Smart reminders

#### GitHub (`github_client.py`)
- Used primarily for self-evolution (committing new skill code)
- View user's repos, issues, PRs
- Create issues

---

### 6. Self-Evolution (`src/evolution/`)

ATLAS can add new capabilities to itself:

```
User: "ATLAS, add the ability to track cryptocurrency prices"
    │
    ▼
1. ATLAS generates a new skill file (crypto_tracker.py)
2. Shows the code to the user for review
3. Runs tests in an isolated sandbox
4. If tests pass, presents a summary of changes
    │
    ▼
   [✅ Approve & Install]  [❌ Reject]
    │
    ▼
5. Registers the new skill in the registry
6. Hot-reloads the module (no bot restart needed)
7. Optionally commits to GitHub
```

**Safety:**
- Sandbox execution in isolated Docker container
- No network access from sandbox (except whitelisted APIs)
- Execution timeout and resource limits
- User must approve all changes before deployment

---

## ⚙️ Configuration Reference

### Required Settings

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_USER_ID` | Your numeric Telegram user ID |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) |

### LLM Provider Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | Primary provider: `anthropic`, `openai`, `ollama` |
| `LLM_MODEL_FAST` | `claude-sonnet-4-20250514` | Model for quick tasks |
| `LLM_MODEL_POWERFUL` | `claude-opus-4-20250514` | Model for complex tasks |
| `LLM_FALLBACK_PROVIDER` | — | Fallback provider if primary fails |
| `LLM_FALLBACK_MODEL` | — | Fallback model |
| `OPENAI_API_KEY` | — | Required if using OpenAI |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3` | Ollama model name |

### Database Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `postgres` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `atlas` | Database name |
| `DB_USER` | `atlas` | Database user |
| `DB_PASSWORD` | — | **Must be set** |

### Optional Integrations

| Variable | Description |
|----------|-------------|
| `GMAIL_CLIENT_ID` | Gmail OAuth2 client ID |
| `GMAIL_CLIENT_SECRET` | Gmail OAuth2 client secret |
| `GMAIL_REFRESH_TOKEN` | Gmail OAuth2 refresh token |
| `GCAL_CLIENT_ID` | Google Calendar OAuth2 client ID |
| `GCAL_CLIENT_SECRET` | Google Calendar OAuth2 client secret |
| `GCAL_REFRESH_TOKEN` | Google Calendar OAuth2 refresh token |
| `GITHUB_TOKEN` | GitHub personal access token |
| `GITHUB_REPO` | GitHub repo for self-evolution (e.g., `user/atlas`) |

### General Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level |
| `TIMEZONE` | `UTC` | Your timezone (e.g., `Europe/Madrid`) |
| `DAILY_BRIEFING_TIME` | `08:00` | When to send daily briefing |
| `LANGUAGE` | `auto` | Force a language or auto-detect |
| `ENCRYPTION_KEY` | — | Fernet key for encrypting sensitive data |

---

## 🐳 Docker Setup

### docker-compose.yml

```yaml
version: '3.8'

services:
  atlas:
    build: .
    container_name: atlas-bot
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      chromadb:
        condition: service_started
    volumes:
      - ./data/exports:/app/data/exports
      - ./data/backups:/app/data/backups
      - ./prompts:/app/prompts

  postgres:
    image: postgres:16-alpine
    container_name: atlas-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${DB_NAME:-atlas}
      POSTGRES_USER: ${DB_USER:-atlas}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-atlas}"]
      interval: 5s
      timeout: 5s
      retries: 5

  chromadb:
    image: chromadb/chroma:latest
    container_name: atlas-vectors
    restart: unless-stopped
    volumes:
      - chroma_data:/chroma/chroma
    environment:
      - ANONYMIZED_TELEMETRY=FALSE

volumes:
  postgres_data:
  chroma_data:
```

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/

# Create data directories
RUN mkdir -p data/exports data/backups

CMD ["python", "-m", "src.main"]
```

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific module tests
pytest tests/test_memory.py -v
pytest tests/test_orchestrator.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run only fast tests (no LLM calls)
pytest tests/ -v -m "not slow"
```

### Test priorities:
1. **Entity extractor**: Correctly extracts projects, skills, contacts from free text
2. **Memory manager**: Stores and retrieves correctly, no duplicates, proper updates
3. **Semantic search**: Finds relevant info with natural queries
4. **Orchestrator**: Correctly classifies intents
5. **Skills**: Each skill produces valid output

---

## 🚀 Deployment

### Option A: VPS (Recommended)

Best for reliability and control. Suggested providers:

| Provider | Plan | Specs | Cost |
|----------|------|-------|------|
| Hetzner Cloud | CX22 | 2 vCPU, 4GB RAM, 40GB SSD | ~€4.50/mo |
| DigitalOcean | Basic | 2 vCPU, 2GB RAM, 50GB SSD | ~$12/mo |
| Contabo | VPS S | 4 vCPU, 8GB RAM, 50GB SSD | ~€6/mo |

```bash
# On your VPS:
sudo apt update && sudo apt install docker.io docker-compose-v2 -y
git clone https://github.com/yourusername/atlas.git
cd atlas
cp .env.example .env
nano .env  # Fill in your values
docker compose up -d

# Optional: Set up automatic updates
# Optional: Configure firewall (ufw)
# Optional: Set up domain + SSL with Caddy for webhook mode
```

### Option B: Railway / Fly.io (Easy)

Deploy directly from GitHub with minimal setup. Higher cost for equivalent resources (~$5-15/mo).

---

## 💰 Cost Estimation

| Component | Monthly Cost |
|-----------|-------------|
| VPS (Hetzner CX22) | ~€4.50 |
| Anthropic API (moderate use) | ~€5-15 |
| Domain (optional) | ~€1 |
| **Total** | **~€10-20** |

Cost optimization tips:
- Use the fast model (Sonnet) for simple tasks, powerful model (Opus) only when needed
- Cache frequent responses
- Batch entity extraction instead of per-message
- Local Ollama as fallback = $0 LLM cost for simple operations

---

## 📋 Implementation Roadmap

### Phase 1: Core (MVP)
- [ ] Project scaffolding (structure, Docker, database)
- [ ] Telegram bot (message handling, commands)
- [ ] LLM client (multi-provider)
- [ ] Memory system: PostgreSQL models + CRUD
- [ ] Entity extractor
- [ ] Semantic storage (ChromaDB)
- [ ] Basic orchestrator (intent classification)
- [ ] Context builder
- [ ] `/me` command (show user profile)
- [ ] `/search` command (semantic search)

### Phase 2: Useful Skills
- [ ] CV generator (PDF + DOCX)
- [ ] Email composer (without Gmail integration)
- [ ] Document generator
- [ ] `/projects` command
- [ ] Periodic summarizer
- [ ] Daily briefing (internal data only)

### Phase 3: External Integrations
- [ ] Gmail integration (OAuth2 + read + send)
- [ ] Google Calendar integration
- [ ] Web researcher
- [ ] Full daily briefing

### Phase 4: Self-Evolution
- [ ] Self-coder (generate new skills)
- [ ] Sandbox execution
- [ ] Hot-reload skills
- [ ] GitHub integration for auto-commits

### Phase 5: Polish
- [ ] Voice message transcription
- [ ] Document processing (PDFs, images, OCR)
- [ ] Automated backups
- [ ] Cost tracking dashboard
- [ ] Rate limiting optimization

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a PR.

### Quick guidelines:
- Follow the existing code patterns and skill interface
- Add tests for new features
- Update documentation for user-facing changes
- One PR per feature/fix

### Adding a new skill:
1. Create `src/skills/your_skill.py` extending `BaseSkill`
2. Implement `execute()` and `can_handle()`
3. Register in `src/skills/registry.py`
4. Add tests in `tests/test_skills.py`
5. Add prompt template in `prompts/skill_prompts/your_skill.yaml`

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🗺️ Roadmap

See the full roadmap in [GitHub Projects](https://github.com/yourusername/atlas/projects).

**Future ideas:**
- WhatsApp channel support
- Web dashboard UI
- Auto-generated portfolio website
- Interview simulation mode
- Interactive contact network graph
- Multi-language voice conversations
- Financial tracking
- Habit tracking with insights
- Integration with Notion, Obsidian, and other PKM tools

---

## ⭐ Star History

If you find ATLAS useful, please consider giving it a star! It helps others discover the project.

---

<p align="center">
  <b>Built with ❤️ by humans (and their digital twins)</b>
</p>
