"""Central orchestrator: classifies intent and routes to the right handler."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.brain.context_builder import Context, ContextBuilder
from src.brain.llm_client import LLMClient
from src.brain.prompts import build_system_prompt
from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


class Intent(str, Enum):
    STORE = "STORE"
    QUERY = "QUERY"
    TASK = "TASK"
    CHAT = "CHAT"
    EVOLVE = "EVOLVE"


INTENT_PROMPT = """Classify the user's intent. Reply with exactly one token from:
STORE, QUERY, TASK, CHAT, EVOLVE.

Definitions:
- STORE: user is sharing information about themselves to remember (job, project, person, event, idea, achievement).
- QUERY: user is asking about their own data ("what did I...", "list my...", "search...", "remember when...").
- TASK: user wants an action performed (generate CV, draft email, create document, write code, daily briefing, research).
- CHAT: casual conversation, opinions, general chit-chat that doesn't fit the above.
- EVOLVE: user requests a new capability/skill ("add the ability to...", "teach yourself to...").

Recent context:
{context}

Message:
\"\"\"{message}\"\"\"

Reply with ONE WORD only."""


@dataclass
class Response:
    text: str
    intent: Intent | None = None
    inline_buttons: list[list[dict[str, str]]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        memory: MemoryManager,
        context_builder: Optional[ContextBuilder] = None,
        skill_registry: Any = None,
        evolution_engine: Any = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.context_builder = context_builder or ContextBuilder(memory)
        self.skill_registry = skill_registry
        self.evolution_engine = evolution_engine

    async def classify_intent(self, message: str, context: Context | None = None) -> Intent:
        ctx_block = context.to_prompt_block() if context else "(none)"
        try:
            raw = await self.llm.complete(
                prompt=INTENT_PROMPT.format(context=ctx_block[:2000], message=message),
                system="You are a strict intent classifier. Reply with one word only.",
                model_tier="fast",
                max_tokens=10,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning("Intent classification failed: %s — defaulting to CHAT", exc)
            return Intent.CHAT
        token = raw.strip().split()[0].upper().rstrip(".,:;")
        try:
            return Intent(token)
        except ValueError:
            logger.warning("Unknown intent '%s' — defaulting to CHAT", token)
            return Intent.CHAT

    async def process_message(self, user_id: int, message: str) -> Response:
        context = await self.context_builder.build(user_id, message)
        intent = await self.classify_intent(message, context)
        context.intent = intent.value

        if intent == Intent.STORE:
            return await self._handle_store(user_id, message, context)
        if intent == Intent.QUERY:
            return await self._handle_query(user_id, message, context)
        if intent == Intent.TASK:
            return await self._handle_task(user_id, message, context)
        if intent == Intent.EVOLVE:
            return await self._handle_evolve(user_id, message, context)
        return await self._handle_chat(user_id, message, context)

    # --- Handlers ------------------------------------------------------
    async def _handle_store(self, user_id: int, message: str, context: Context) -> Response:
        result = await self.memory.process_and_store(
            user_id=user_id,
            message=message,
            context_hint=context.to_prompt_block()[:2000],
        )
        if result.extraction.is_empty():
            return await self._handle_chat(user_id, message, context)

        bullets = self._format_extraction_bullets(result.extraction)
        text = "Saved:\n" + bullets + "\n\nLet me know if anything's off."
        return Response(text=text, intent=Intent.STORE, metadata={"summary": result.stored_summary})

    async def _handle_query(self, user_id: int, message: str, context: Context) -> Response:
        system = build_system_prompt(
            user_name=context.profile.get("name"),
            living_profile=context.living_profile,
            communication_style=context.profile.get("communication_style"),
        )
        body = (
            f"User asked: {message}\n\n"
            f"Context to ground the answer:\n{context.to_prompt_block()}\n\n"
            "Answer using only the facts above. If unknown, say so. Be concise."
        )
        text = await self.llm.complete(
            prompt=body, system=system, model_tier="fast", max_tokens=800,
        )
        return Response(text=text, intent=Intent.QUERY)

    async def _handle_task(self, user_id: int, message: str, context: Context) -> Response:
        if self.skill_registry is None:
            return await self._handle_chat(user_id, message, context)
        skill = self.skill_registry.find_best_skill(message, context)
        if skill is None:
            return await self._handle_chat(user_id, message, context)
        sr = await skill.execute(message=message, context=context)
        return Response(
            text=sr.text,
            intent=Intent.TASK,
            inline_buttons=sr.inline_buttons,
            files=sr.files,
            metadata={"skill": skill.name, **sr.metadata},
        )

    async def _handle_evolve(self, user_id: int, message: str, context: Context) -> Response:
        if self.evolution_engine is None:
            return Response(
                text="Self-evolution isn't enabled in this build. "
                     "Configure GITHUB_TOKEN to allow OMNIME to write new skills.",
                intent=Intent.EVOLVE,
            )
        return await self.evolution_engine.handle(user_id=user_id, message=message, context=context)

    async def _handle_chat(self, user_id: int, message: str, context: Context) -> Response:
        system = build_system_prompt(
            user_name=context.profile.get("name"),
            living_profile=context.living_profile,
            communication_style=context.profile.get("communication_style"),
        )
        body = f"{context.to_prompt_block()}\n\nUser: {message}\n\nReply naturally."
        text = await self.llm.complete(prompt=body, system=system, model_tier="fast", max_tokens=600)
        return Response(text=text, intent=Intent.CHAT)

    # --- Helpers -------------------------------------------------------
    @staticmethod
    def _format_extraction_bullets(extraction: Any) -> str:
        lines: list[str] = []
        for p in extraction.projects:
            lines.append(f"  - Project: {p.get('name')}")
        for w in extraction.work_experience:
            lines.append(f"  - Job: {w.get('role')} @ {w.get('company')}")
        for e in extraction.education:
            lines.append(f"  - Education: {e.get('degree') or ''} @ {e.get('institution')}")
        for s in extraction.skills:
            lines.append(f"  - Skill: {s.get('name')}")
        for c in extraction.contacts:
            lines.append(f"  - Contact: {c.get('name')}")
        for a in extraction.achievements:
            lines.append(f"  - Achievement: {a.get('title')}")
        for ev in extraction.life_events:
            lines.append(f"  - Event: {ev.get('title')}")
        for i in extraction.ideas:
            lines.append(f"  - Idea: {i.get('content')[:80]}")
        return "\n".join(lines) if lines else "  (no new entities)"
