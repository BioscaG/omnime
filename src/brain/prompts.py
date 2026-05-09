"""Dynamic system-prompt builder backed by YAML templates."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.config import settings


logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _load_yaml(name: str) -> dict[str, Any]:
    path: Path = settings.prompts_dir / name
    if not path.exists():
        logger.warning("Prompt file missing: %s", path)
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def render(template: str, **values: Any) -> str:
    """Format a template using {placeholder} syntax, ignoring missing keys."""
    class _D(dict):
        def __missing__(self, key: str) -> str:  # noqa: D401
            return "{" + key + "}"

    return template.format_map(_D(values))


def build_system_prompt(
    *,
    user_name: str | None = None,
    living_profile: str | None = None,
    communication_style: str | None = None,
    active_projects: list[str] | None = None,
    upcoming_events: list[str] | None = None,
    pending_tasks: list[str] | None = None,
    extra: str | None = None,
) -> str:
    base = _load_yaml("system_base.yaml").get("base_identity", DEFAULT_BASE_IDENTITY)
    personality = _load_yaml("personality.yaml").get("personality", "")

    return render(
        base,
        user_name=user_name or "the user",
        extracted_personality=living_profile or personality or "(unknown — learn as you go)",
        communication_style=communication_style or "neutral, friendly, concise",
        active_projects=", ".join(active_projects or []) or "(none recorded)",
        upcoming_events=", ".join(upcoming_events or []) or "(none)",
        pending_tasks=", ".join(pending_tasks or []) or "(none)",
        extra=extra or "",
    )


def skill_prompt(name: str) -> dict[str, Any]:
    return _load_yaml(f"skill_prompts/{name}.yaml")


DEFAULT_BASE_IDENTITY = """You are OMNIME, the personal AI assistant of {user_name}.
Your goal is to be their digital twin: you know their history, their communication
style, their projects, and you act on their behalf when asked.

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
{extra}
"""
