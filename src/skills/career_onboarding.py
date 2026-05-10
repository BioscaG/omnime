"""Onboarding interview: asks the user the right questions to fill the profile.

Uses a multi-turn conversation flow stored in the application's bot_data so
the user can answer over time. Each batch of answers is fed back through the
extractor so the whole structured store gets populated.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


QUESTION_BANK: list[tuple[str, str]] = [
    ("identity", "What's your full name and where are you based?"),
    ("current_role", "What's your current role? Title, company, how long, what you actually do day-to-day."),
    ("strongest_project", "Walk me through your strongest project (last 12 months). Tech, role, outcome with numbers if you have them."),
    ("notable_past_role", "One past role that mattered. Why was it formative?"),
    ("education", "Education in 2 lines: degree, institution, anything notable."),
    ("technical_skills", "Top 5-10 technical skills, with proficiency (beginner/intermediate/advanced/expert) and years."),
    ("soft_skills", "2-4 soft skills you'd put on a CV, with 1-line evidence each."),
    ("achievements", "Top 3 achievements you'd want a recruiter to notice. Quantify if possible."),
    ("network", "3-5 people in your professional network: name, relationship, why they matter."),
    ("goals_career", "What's the next role / move you're aiming for? Timeline?"),
    ("communication_style", "How would friends describe how you write/speak? (formal, dry, warm, blunt, etc.)"),
    ("personal_focus", "Any non-work pursuits you're serious about? Hobbies, sports, creative work, languages."),
    ("decisions_open", "Any open decisions you're chewing on right now?"),
]


@dataclass
class OnboardingState:
    index: int = 0
    answers: dict[str, str] = field(default_factory=dict)
    started: bool = False


class CareerOnboardingSkill(BaseSkill):
    name = "career_onboarding"
    description = "Onboarding interview to populate the user's profile fully."
    triggers = [
        "/onboard", "/onboarding", "interview me", "build my profile",
        "ask me questions", "career onboarding",
    ]

    _STATE_KEY = "_career_onboarding_state"

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower().strip()
        if m.startswith("/onboard"):
            return 0.95
        if any(t in m for t in ("interview me", "build my profile", "ask me questions")):
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        state: OnboardingState = self._get_state(context, OnboardingState())
        cmd = message.strip().lower()

        if cmd in ("/onboard", "/onboarding") or not state.started:
            state = OnboardingState(started=True, index=0)
            self._set_state(context, state)
            key, question = QUESTION_BANK[0]
            return SkillResponse(
                text=(
                    f"📝 Onboarding interview ({len(QUESTION_BANK)} short questions). "
                    "Answer in your own words, free-form. Type /skip to jump a question, /done to finish early.\n\n"
                    f"Q1/{len(QUESTION_BANK)}: {question}"
                ),
                metadata={"onboarding": True, "current_key": key},
            )

        if cmd == "/done":
            return await self._finalise(context, state)
        if cmd == "/skip":
            state.index += 1
            if state.index >= len(QUESTION_BANK):
                return await self._finalise(context, state)
            return self._next(state, skipped=True)

        # Capture the answer for the *current* question.
        key, _ = QUESTION_BANK[state.index]
        state.answers[key] = message.strip()
        state.index += 1

        if state.index >= len(QUESTION_BANK):
            return await self._finalise(context, state)
        return self._next(state, skipped=False)

    def _next(self, state: OnboardingState, skipped: bool) -> SkillResponse:
        key, question = QUESTION_BANK[state.index]
        prefix = "Skipped." if skipped else "Got it."
        return SkillResponse(
            text=f"{prefix}\n\nQ{state.index + 1}/{len(QUESTION_BANK)}: {question}",
            metadata={"onboarding": True, "current_key": key},
        )

    async def _finalise(self, context: "Context", state: OnboardingState) -> SkillResponse:
        merged = "\n\n".join(
            f"## {k.replace('_', ' ').title()}\n{v}" for k, v in state.answers.items() if v
        )
        if not merged:
            self._clear_state(context)
            return SkillResponse(text="Nothing captured. Run /onboard again whenever.")
        result = await self.memory.process_and_store(
            user_id=context.user_id,
            message=merged,
            context_hint=context.to_prompt_block()[:1500],
        )
        self._clear_state(context)
        # Trigger living-profile refresh.
        try:
            import asyncio

            asyncio.create_task(self.memory.summarizer.update_living_profile(context.user_id))
        except Exception:
            pass
        return SkillResponse(
            text=(
                "🎉 Onboarding complete.\n"
                f"Captured: {result.stored_summary}.\n"
                "Use /me to see what I now know."
            ),
            metadata={"onboarding_done": True},
        )

    # --- State helpers (per-user, in-process) ---------------------------
    def _get_state(self, context: "Context", default: OnboardingState) -> OnboardingState:
        store = getattr(self, "_states", None) or {}
        self._states = store
        return store.get(context.user_id, default)

    def _set_state(self, context: "Context", state: OnboardingState) -> None:
        self._states = getattr(self, "_states", {}) | {context.user_id: state}

    def _clear_state(self, context: "Context") -> None:
        states = getattr(self, "_states", {})
        states.pop(context.user_id, None)
        self._states = states
