"""Adaptive journaling skill: prompt → entry → store + sentiment."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


PROMPT_GENERATOR = """Write ONE journaling prompt for the user, tuned to:
- Their living profile: {profile}
- Recent activity: {recent}
- Today's date: {today}

Rules:
- Single short prompt (≤25 words).
- Forward-looking when their week's been rough; reflective when it's been heavy.
- Avoid generic prompts ('how are you?'). Be specific.
- Output the prompt only."""


SENTIMENT_PROMPT = """Score the emotional valence of this journal entry on a scale -1.0 (very negative) to +1.0 (very positive).
Reply with a single float, no other text.

Entry:
\"\"\"{entry}\"\"\""""


class JournalingSkill(BaseSkill):
    name = "journaling"
    description = "Daily journaling prompt that captures sentiment and threads themes over time."
    triggers = [
        "/journal", "journal entry", "journaling prompt", "write in journal",
        "diario de hoy", "quiero escribir en el diario",
    ]
    examples = ["empezamos el journal de hoy", "/journal hoy me siento agotado"]

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower()
        if m.startswith("/journal"):
            return 0.95
        if "journaling" in m or "journal entry" in m:
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        rest = message.strip()
        if rest.lower().startswith("/journal"):
            rest = rest[len("/journal"):].strip()

        if not rest:
            prompt = await self._generate_prompt(context)
            return SkillResponse(
                text=f"📔 _{prompt}_\n\nReply with /journal <your entry> to log it.",
                metadata={"prompt": prompt},
            )

        sentiment = await self._sentiment(rest)
        # Persist as life event with sentiment in metadata + index semantically.
        with session_scope() as s:
            store = StructuredStore(s)
            store.add_life_event(
                user_id=context.user_id,
                title=f"Journal {date.today().isoformat()}",
                description=rest[:5000],
                category="journal",
                date=date.today(),
                metadata={"sentiment": sentiment},
            )
        try:
            self.memory.semantic.add(
                collection="conversations",
                text=rest,
                metadata={
                    "user_id": context.user_id,
                    "category": "journal",
                    "sentiment": sentiment,
                    "date": date.today().isoformat(),
                },
            )
        except Exception:
            pass

        mood_emoji = "🙂" if sentiment > 0.3 else "😐" if sentiment > -0.3 else "🙁"
        return SkillResponse(
            text=f"Logged. Sentiment {mood_emoji} ({sentiment:+.2f}).",
            metadata={"sentiment": sentiment},
        )

    async def _generate_prompt(self, context: "Context") -> str:
        recent = "\n".join(
            f"{m['role']}: {m['text'][:200]}" for m in (context.recent_messages or [])[-5:]
        ) or "(no recent activity)"
        prompt = await self.llm.complete(
            prompt=PROMPT_GENERATOR.format(
                profile=(context.living_profile or context.profile.get("bio") or "(unknown)")[:1500],
                recent=recent,
                today=date.today().isoformat(),
            ),
            system="You write a single, sharp journaling prompt.",
            model_tier="fast",
            max_tokens=80,
            temperature=0.6,
        )
        return prompt.strip().strip("\"'")

    async def _sentiment(self, entry: str) -> float:
        try:
            raw = await self.llm.complete(
                prompt=SENTIMENT_PROMPT.format(entry=entry[:2000]),
                system="You are a careful sentiment scorer.",
                model_tier="fast",
                max_tokens=10,
                temperature=0.0,
            )
            return float(raw.strip().split()[0])
        except Exception:
            return 0.0
