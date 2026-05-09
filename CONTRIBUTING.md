# Contributing to ATLAS

Thanks for your interest in improving ATLAS. This guide covers how to set up
your environment, the conventions used in the codebase, and how to add new
skills.

## Local development

```bash
git clone <your fork>
cd atlas
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in real values
```

For databases, the easiest path is `docker compose up -d postgres chromadb`
and then run the bot directly: `python -m src.main`.

## Running tests

```bash
pytest -v
pytest -v -m "not slow"          # skip tests that hit real APIs
pytest --cov=src --cov-report=html
```

The default test suite uses an in-memory SQLite database and a fake LLM —
no API keys or running services required.

## Coding conventions

- Python 3.12+, type hints everywhere except trivial helpers.
- Async code uses `asyncio` and `python-telegram-bot` v20+.
- Public APIs documented with short docstrings; comment only the *why*.
- Keep modules cohesive: one concept per file.
- New external dependencies need justification in the PR description.

Run `ruff check src tests` before pushing.

## Adding a new skill

1. Create `src/skills/your_skill.py` extending `BaseSkill`:

   ```python
   from src.skills.base import BaseSkill, SkillResponse

   class YourSkill(BaseSkill):
       name = "your_skill"
       description = "What it does"
       triggers = ["/your_skill", "trigger phrase"]

       def __init__(self, llm, memory):
           self.llm = llm
           self.memory = memory

       def can_handle(self, message: str, intent: str | None = None) -> float:
           ...

       async def execute(self, message, context) -> SkillResponse:
           ...
   ```

2. Register it in `src/skills/registry.py` in `_register_default_skills`.
3. Add tests in `tests/test_skills.py`.
4. Add a prompt template under `prompts/skill_prompts/your_skill.yaml` if
   the skill needs configurable guidance.

## Pull requests

- One change per PR. Keep the diff focused.
- Include reproduction or test plan.
- Tests must pass on CI.
- Update `README.md` / `SETUP_GUIDES.md` for any user-visible change.

## Reporting security issues

Email the maintainer directly rather than filing a public issue.
