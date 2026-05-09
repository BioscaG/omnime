"""Builds the most relevant context for each user message."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.memory.manager import MemoryManager


@dataclass
class Context:
    user_id: int
    living_profile: str = ""
    profile: dict[str, Any] = field(default_factory=dict)
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    semantic_hits: list[dict[str, Any]] = field(default_factory=list)
    intent: str | None = None

    def to_prompt_block(self) -> str:
        parts: list[str] = []
        if self.living_profile:
            parts.append(f"LIVING PROFILE:\n{self.living_profile}")
        if self.profile:
            top_skills = ", ".join(s["name"] for s in (self.profile.get("skills") or [])[:15])
            active = ", ".join(
                p["name"] for p in (self.profile.get("projects") or []) if p.get("status") == "active"
            )
            if active:
                parts.append(f"ACTIVE PROJECTS: {active}")
            if top_skills:
                parts.append(f"TOP SKILLS: {top_skills}")
        if self.semantic_hits:
            hits = "\n".join(
                f"- {h['text'][:300]}" for h in self.semantic_hits[:8]
            )
            parts.append(f"RELEVANT MEMORY:\n{hits}")
        if self.recent_messages:
            convo = "\n".join(
                f"{m['role']}: {m['text'][:300]}" for m in self.recent_messages[-8:]
            )
            parts.append(f"RECENT CONVERSATION:\n{convo}")
        return "\n\n".join(parts)


class ContextBuilder:
    def __init__(self, memory: MemoryManager) -> None:
        self.memory = memory

    async def build(
        self,
        user_id: int,
        message: str,
        intent: str | None = None,
        max_semantic: int = 8,
        recent_n: int = 10,
    ) -> Context:
        profile = self.memory.get_user_profile(user_id)
        living_profile = profile.get("living_profile") or profile.get("bio") or ""

        try:
            hits = self.memory.semantic_search(user_id, message, n_results=max_semantic)
            semantic_hits = [
                {"text": h.text, "metadata": h.metadata, "distance": h.distance}
                for h in hits
            ]
        except Exception:
            semantic_hits = []

        recent = self.memory.recent_messages(user_id, limit=recent_n)

        return Context(
            user_id=user_id,
            living_profile=living_profile,
            profile=profile,
            recent_messages=recent,
            semantic_hits=semantic_hits,
            intent=intent,
        )
