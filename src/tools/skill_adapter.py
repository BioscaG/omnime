"""Adapter that exposes a Skill as a primitive Tool.

For *compound* skills — multi-step actions like driving a Chromium browser,
generating a CV, or producing a daily briefing — we don't want to
decompose them further. They're already self-contained agents; the outer
loop just needs to be able to *call* them as one tool. This adapter
wraps any ``BaseSkill`` so it shows up alongside atomic primitives in the
agentic loop's tool catalog.

The skill's ``execute_with_args`` is invoked with the model-supplied
arguments. Its ``SkillResponse.text`` is fed back as the tool result —
so the loop driver can decide whether to follow up with another tool or
write the final user-facing answer.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from src.tools import Tool

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.skills.base import BaseSkill


def wrap_skill_as_tool(skill: "BaseSkill") -> Tool:
    desc = skill.description
    if getattr(skill, "examples", None):
        desc = desc + "  Examples: " + " | ".join(skill.examples[:2])

    async def runner(args: dict, context: "Context") -> str:
        sr = await skill.execute_with_args(args, context)
        # Surface inline buttons / files as a side-effect on the context so
        # the orchestrator can attach them to the final response.
        side = getattr(context, "_tool_side_effects", None)
        if side is None:
            side = {}
            setattr(context, "_tool_side_effects", side)
        if sr.inline_buttons:
            side.setdefault("inline_buttons", []).extend(sr.inline_buttons)
        if sr.files:
            side.setdefault("files", []).extend(sr.files)
        # Hand the textual output back to the model. Truncate to keep cost down.
        text = (sr.text or "")[:6000]
        meta = {k: v for k, v in (sr.metadata or {}).items() if k not in {"draft"}}
        if not meta:
            return text or "(no output)"
        return json.dumps({"text": text, "meta": meta}, ensure_ascii=False)

    return Tool(
        name=skill.name,
        description=desc,
        input_schema=skill.input_schema or {
            "type": "object", "properties": {}, "required": []
        },
        run=runner,
    )
