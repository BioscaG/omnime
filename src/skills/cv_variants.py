"""Generate two CV variants for the same job and let the user pick.

Persists both, marks the chosen one and stores feedback so future generations
bias toward the winning style.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.config import settings
from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.base import BaseSkill, SkillResponse
from src.skills.cv_generator import CVGeneratorSkill

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


VARIANT_STYLES = [
    ("impact", "Lead with quantified impact and outcomes."),
    ("narrative", "Lead with a clear narrative arc through roles and projects."),
]


class CVVariantsSkill(BaseSkill):
    name = "cv_variants"
    description = "Generate two CV variants for the same job and pick the winner."
    triggers = ["/cv_variants", "cv variants", "two cv versions", "ab cv", "a/b cv"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/cv_variants"):
            return 0.95
        if "cv variants" in m or "two cv versions" in m or "a/b cv" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        target = self._extract_target(message)
        profile = context.profile or {}
        winning_style = self._winning_style(context.user_id) or "impact"

        # Ensure the historically-winning style runs first.
        ordered = sorted(VARIANT_STYLES, key=lambda s: 0 if s[0] == winning_style else 1)

        cv_skill = CVGeneratorSkill(self.llm, self.memory)
        variants: list[dict[str, Any]] = []
        for style, guidance in ordered:
            extra_prompt = (
                f"\nStyle preference: {guidance}\n"
                f"Target / job description:\n{target or '(general purpose)'}\n"
            )
            markdown = await cv_skill._render_cv(profile=profile, job_description=target)
            markdown = f"<!-- variant: {style} -->\n{markdown}{extra_prompt}"
            variants.append({"style": style, "markdown": markdown})

        with session_scope() as s:
            store = StructuredStore(s)
            stored_ids = []
            for v in variants:
                row = store.add_cv_variant(
                    user_id=context.user_id,
                    label=f"{v['style']} {datetime.utcnow():%Y-%m-%d %H:%M}",
                    body_markdown=v["markdown"],
                    style=v["style"],
                )
                stored_ids.append(row.id)

        return SkillResponse(
            text=(
                f"Generated 2 CV variants ({ordered[0][0]} vs {ordered[1][0]}).\n"
                "Reply with `/cv_pick <id>` once you decide."
            ),
            metadata={"variant_ids": stored_ids, "target": target},
            inline_buttons=[[
                {"text": f"✅ Pick {ordered[0][0]}", "callback_data": f"cv_pick:{stored_ids[0]}"},
                {"text": f"✅ Pick {ordered[1][0]}", "callback_data": f"cv_pick:{stored_ids[1]}"},
            ]],
        )

    @staticmethod
    def _extract_target(message: str) -> str | None:
        m = message.strip()
        for tok in ("/cv_variants",):
            if m.lower().startswith(tok):
                rest = m[len(tok):].strip()
                return rest or None
        return m or None

    def _winning_style(self, user_id: int) -> str | None:
        with session_scope() as s:
            store = StructuredStore(s)
            chosen = [v for v in store.list_cv_variants(user_id) if v.chosen and v.style]
            if not chosen:
                return None
            counts: dict[str, int] = {}
            for v in chosen:
                counts[v.style] = counts.get(v.style, 0) + 1
            return max(counts, key=counts.get)


class SkillGapSkill(BaseSkill):
    name = "skill_gap"
    description = "Compare your stored skills against a job description."
    triggers = ["/gap", "/skill_gap", "skill gap", "what's missing", "gap analysis"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/gap") or m.startswith("/skill_gap"):
            return 0.95
        if "skill gap" in m or "gap analysis" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        target = message.strip()
        for tok in ("/gap", "/skill_gap"):
            if target.lower().startswith(tok):
                target = target[len(tok):].strip()
        if not target:
            return SkillResponse(text="Paste the job description after /gap so I can compare.")

        skills = (context.profile or {}).get("skills") or []
        skill_block = ", ".join(s["name"] for s in skills)
        prompt = (
            "Compare the user's stored skills against the target role and identify gaps.\n\n"
            f"Stored skills: {skill_block or '(none)'}\n\n"
            f"Target role / job description:\n{target}\n\n"
            "Output:\n"
            "1. Strong matches (3-5)\n"
            "2. Missing or weak (3-5)\n"
            "3. Quick wins to close the gap (1-2 weeks)\n"
            "4. Longer plays (>1 month)\n"
            "Be concrete."
        )
        text = await self.llm.complete(
            prompt=prompt,
            system="You are a senior career coach.",
            model_tier="powerful",
            max_tokens=1500,
        )
        return SkillResponse(text=text)
