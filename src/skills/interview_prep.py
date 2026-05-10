"""Interview prep skill: STAR-format simulator for a target role."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


PREP_PROMPT = """You are running an interview prep session for {name}.

Target role / company:
{target}

Their stored profile (use only what's there):
{profile}

Their structured projects + achievements:
{evidence}

Output a structured prep packet:

1. Likely behavioral questions (5) — common ones for this role.
2. STAR-formatted answers using ONLY their stored evidence. If a detail isn't there,
   leave a {{placeholder}} and mark it `[ASK USER]`.
3. Likely technical questions (5) — match the role's seniority + stack.
4. Talking points / war stories from their stored projects most relevant for this role.
5. 3 questions THEY should ask the interviewer.

Keep it tight. Markdown."""


class InterviewPrepSkill(BaseSkill):
    name = "interview_prep"
    description = "Prepare STAR-format answers and questions for a specific interview."
    triggers = [
        "/prep", "/interview_prep", "interview prep",
        "prep for an interview", "star answers", "behavioral questions",
    ]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/prep") or m.startswith("/interview"):
            return 0.95
        if "interview prep" in m or "star answers" in m or "behavioral questions" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        target = self._strip_command(message) or "(role context not provided)"
        profile = context.profile or {}
        evidence = self._evidence_block(profile)
        text = await self.llm.complete(
            prompt=PREP_PROMPT.format(
                name=profile.get("name") or "the user",
                target=target,
                profile=context.living_profile or profile.get("bio") or "(unknown)",
                evidence=evidence,
            ),
            system="You are an interview coach. Be honest about gaps.",
            model_tier="powerful",
            max_tokens=2500,
        )
        return SkillResponse(text=text, metadata={"target": target})

    @staticmethod
    def _evidence_block(profile: dict) -> str:
        lines: list[str] = []
        for p in (profile.get("projects") or [])[:10]:
            lines.append(
                f"- Project '{p['name']}' ({p.get('status')}): {p.get('description') or ''}"
            )
        for w in (profile.get("work_experience") or [])[:5]:
            lines.append(f"- Job: {w['role']} @ {w['company']} ({w.get('start_date')} → {w.get('end_date') or 'now'})")
        for s in (profile.get("skills") or [])[:20]:
            lines.append(f"- Skill: {s['name']} ({s.get('proficiency')})")
        return "\n".join(lines) or "(empty profile)"

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        for tok in ("/prep", "/interview_prep", "/interview"):
            if m.lower().startswith(tok):
                return m.split(maxsplit=1)[1].strip() if len(m.split(maxsplit=1)) > 1 else ""
        return m
