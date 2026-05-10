"""Atomic capability primitives for the agentic loop.

Skills (in ``src/skills/``) are *acciones pre-cocinadas* — they package an
intent + execution + UI rendering and are invoked through slash commands.
Tools, in contrast, are **primitives**: small, single-purpose functions
that take typed args, do one thing, and return raw structured data
(usually JSON-as-string). The agentic loop driver model sees a catalog of
tools and *decides* which ones to compose for any given request.

Example: instead of an opinionated ``email_inbox`` skill that always
'lists 10 unread emails formatted with buttons', the email tools layer
exposes ``gmail_list``, ``gmail_read``, ``gmail_search``, ``gmail_send`` —
and the model itself composes the natural-language response from the data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.brain.context_builder import Context


@dataclass
class Tool:
    """A primitive capability invocable from the agentic loop."""

    name: str
    description: str
    input_schema: dict
    run: Callable[[dict, "Context"], Awaitable[str]]
    """``run(args, context)`` — must return a string. JSON-string is preferred
    for structured returns; the driver model handles JSON natively."""

    side_effects: dict | None = None
    """Optional metadata describing side effects of this tool (e.g.
    ``{"sent_email_id": "..."}``). Set by ``run`` via context, not used by
    the loop directly — used to inject UI hooks (cancel buttons, etc.)
    into the orchestrator's final Response."""


def tool_to_def(tool: Tool):
    """Convert a Tool to the LLMClient's ToolDef so it can be passed to
    Anthropic tool-use directly."""
    from src.brain.llm_client import ToolDef

    return ToolDef(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
    )


def collect_default_tools(skill_registry=None) -> list[Tool]:
    """Aggregate every primitive available to the agentic loop.

    Hierarchy:

    1. **Atomic primitives** (this file's siblings) — Gmail CRUD, memory
       search/save, web fetch/search, etc. Each does one thing, returns
       JSON, no LLM call inside.

    2. **Compound primitives** — current Skills wrapped via
       ``wrap_skill_as_tool``. These are self-contained sub-agents
       (``browser_run`` runs a vision-driven Chromium loop;
       ``cv_generator`` orchestrates Opus over the user's full profile)
       that we don't want to decompose further. The outer loop calls them
       like any other tool. Email skills are excluded here — their
       primitives already cover the same surface.
    """
    from src.tools.email_tools import build_email_tools
    from src.tools.memory_tools import build_memory_tools
    from src.tools.web_tools import build_web_tools
    from src.tools.calendar_tools import build_calendar_tools
    from src.tools.notion_tools import build_notion_tools
    from src.tools.github_tools import build_github_tools
    from src.tools.files_tools import build_files_tools
    from src.tools.skill_adapter import wrap_skill_as_tool

    tools: list[Tool] = []
    tools.extend(build_email_tools())
    tools.extend(build_calendar_tools())
    tools.extend(build_memory_tools())
    tools.extend(build_files_tools())
    tools.extend(build_web_tools())
    tools.extend(build_notion_tools())
    tools.extend(build_github_tools())

    # Wrap compound skills. Email + web_fetch + web_researcher are
    # superseded by atomic primitives above.
    SKILL_BLOCKLIST = {
        "email_inbox", "email_read", "email_search", "email_composer",
        "web_fetch", "web_researcher",
    }
    if skill_registry is not None:
        for skill in skill_registry.list_enabled():
            if skill.name in SKILL_BLOCKLIST:
                continue
            tools.append(wrap_skill_as_tool(skill))

    return tools

