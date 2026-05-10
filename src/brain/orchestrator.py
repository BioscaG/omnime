"""Central orchestrator: routes via Anthropic tool use with a regex fast-path."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from typing import TYPE_CHECKING

from src.brain.context_builder import Context, ContextBuilder
from src.brain.llm_client import LLMClient, ToolDef
from src.brain.prompts import build_system_prompt

if TYPE_CHECKING:
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


class Intent(str, Enum):
    STORE = "STORE"
    QUERY = "QUERY"
    TASK = "TASK"
    CHAT = "CHAT"
    EVOLVE = "EVOLVE"


# --- Routing tools ----------------------------------------------------------

ROUTING_TOOLS: list[ToolDef] = [
    ToolDef(
        name="STORE",
        description=(
            "Persist new factual information the user shared about themselves: "
            "projects, jobs, education, skills, contacts, achievements, life events, ideas."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    ToolDef(
        name="QUERY",
        description=(
            "Answer a question about the user's own stored data ('what did I…', 'list my…', "
            "'remember when…', 'who introduced me to…')."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    ToolDef(
        name="TASK",
        description=(
            "Run a known skill: CV generation, email drafting, document creation, "
            "web research, daily briefing, code generation, weekly review."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Hint for the skill name when obvious (e.g. 'cv_generator').",
                }
            },
            "required": [],
        },
    ),
    ToolDef(
        name="EVOLVE",
        description=(
            "User asks OMNIME to gain a brand new capability ('add the ability to…', "
            "'teach yourself to…')."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    ToolDef(
        name="CHAT",
        description="Casual conversation, opinions or anything that doesn't fit the others.",
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
]


# Quick regex fast-path: skip the LLM for obvious cases.
_FASTPATH_PATTERNS: list[tuple[Intent, re.Pattern[str]]] = [
    (Intent.EVOLVE, re.compile(r"^\s*evolve[:\s]", re.I)),
    (Intent.EVOLVE, re.compile(r"\b(add the ability|teach yourself|new skill|learn how)\b", re.I)),
    (Intent.TASK, re.compile(r"^/(cv|cv_for|email|briefing|research|review|code)\b", re.I)),
    (Intent.QUERY, re.compile(r"^\s*(what|who|when|where|which|how many) (did|do|are|is|was) i\b", re.I)),
    (Intent.QUERY, re.compile(r"^/search\b", re.I)),
]


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
        memory: "MemoryManager",
        context_builder: Optional[ContextBuilder] = None,
        skill_registry: Any = None,
        evolution_engine: Any = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.context_builder = context_builder or ContextBuilder(memory)
        self.skill_registry = skill_registry
        self.evolution_engine = evolution_engine

    async def classify_intent(
        self,
        message: str,
        context: Context | None = None,
    ) -> tuple[Intent, dict[str, Any]]:
        # 1. Fast-path: cheap regex hits return immediately, no LLM call.
        for intent, pattern in _FASTPATH_PATTERNS:
            if pattern.search(message):
                return intent, {"source": "regex"}

        # 2. Tool-use routing (provider-aware).
        ctx_block = context.to_prompt_block() if context else "(none)"
        try:
            result = await self.llm.use_tools(
                prompt=(
                    "Pick the right tool to handle this user message.\n\n"
                    f"Recent context:\n{ctx_block[:2000]}\n\n"
                    f'User message: "{message}"'
                ),
                tools=ROUTING_TOOLS,
                system="You route messages by selecting exactly one tool.",
                model_tier="fast",
                max_tokens=200,
                temperature=0.0,
                tool_choice="any",
            )
        except Exception as exc:
            logger.warning("Tool-use intent routing failed: %s — defaulting to CHAT", exc)
            return Intent.CHAT, {"source": "fallback", "error": str(exc)}

        if result.tool_calls:
            call = result.tool_calls[0]
            try:
                return Intent(call.name), {"source": "tool_use", "input": call.input}
            except ValueError:
                logger.warning("Unknown tool '%s' — defaulting to CHAT", call.name)
        return Intent.CHAT, {"source": "tool_use_no_call"}

    async def process_message(
        self,
        user_id: int,
        message: str,
        private: bool = False,
        stream_message: Any = None,
    ) -> Response:
        context = await self.context_builder.build(user_id, message)
        if private:
            return await self._handle_private(user_id, message, context, stream_message=stream_message)

        intent, route_meta = await self.classify_intent(message, context)
        context.intent = intent.value

        if intent == Intent.STORE:
            return await self._handle_store(user_id, message, context)
        if intent == Intent.QUERY:
            return await self._handle_query(user_id, message, context, stream_message=stream_message)
        if intent == Intent.TASK:
            return await self._handle_task(user_id, message, context, hint=route_meta.get("input", {}))
        if intent == Intent.EVOLVE:
            return await self._handle_evolve(user_id, message, context)
        return await self._handle_chat(user_id, message, context, stream_message=stream_message)

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

        # Refresh living profile asynchronously when something meaningful changed.
        if self._is_meaningful(result.extraction):
            try:
                import asyncio

                asyncio.create_task(self.memory.summarizer.update_living_profile(user_id))
            except Exception as exc:
                logger.warning("Could not schedule living profile refresh: %s", exc)

        text = "Saved:\n" + bullets + "\n\nLet me know if anything's off."
        return Response(text=text, intent=Intent.STORE, metadata={"summary": result.stored_summary})

    async def _handle_query(
        self,
        user_id: int,
        message: str,
        context: Context,
        stream_message: Any = None,
    ) -> Response:
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
        text = await self._stream_or_complete(
            stream_message, body, system, max_tokens=800,
        )
        return Response(text=text, intent=Intent.QUERY)

    async def _handle_task(
        self,
        user_id: int,
        message: str,
        context: Context,
        hint: dict[str, Any] | None = None,
    ) -> Response:
        if self.skill_registry is None:
            return await self._handle_chat(user_id, message, context)
        skill = None
        if hint and hint.get("skill"):
            skill = self.skill_registry.get(hint["skill"])
        if skill is None:
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

    async def _handle_chat(
        self,
        user_id: int,
        message: str,
        context: Context,
        stream_message: Any = None,
    ) -> Response:
        system = build_system_prompt(
            user_name=context.profile.get("name"),
            living_profile=context.living_profile,
            communication_style=context.profile.get("communication_style"),
        )
        body = f"{context.to_prompt_block()}\n\nUser: {message}\n\nReply naturally."
        text = await self._stream_or_complete(
            stream_message, body, system, max_tokens=600,
        )
        return Response(text=text, intent=Intent.CHAT)

    async def _stream_or_complete(
        self,
        stream_message: Any,
        prompt: str,
        system: str,
        max_tokens: int,
    ) -> str:
        if stream_message is None or self.llm.provider != "anthropic":
            return await self.llm.complete(
                prompt=prompt, system=system, model_tier="fast", max_tokens=max_tokens,
            )
        from src.bot.streaming import TelegramStreamer

        streamer = TelegramStreamer(stream_message)
        try:
            async for chunk in self.llm.stream(
                prompt=prompt, system=system, model_tier="fast", max_tokens=max_tokens,
            ):
                await streamer.push(chunk)
            return await streamer.finalize()
        except Exception as exc:
            logger.warning("Streaming failed (%s); falling back to non-streamed", exc)
            return await self.llm.complete(
                prompt=prompt, system=system, model_tier="fast", max_tokens=max_tokens,
            )

    async def _handle_private(
        self,
        user_id: int,
        message: str,
        context: Context,
        stream_message: Any = None,
    ) -> Response:
        system = build_system_prompt(
            user_name=context.profile.get("name"),
            living_profile=context.living_profile,
            communication_style=context.profile.get("communication_style"),
            extra="\nThis is a /private message: nothing said here will be persisted to long-term memory.",
        )
        try:
            text = await self.llm.complete(
                prompt=f"User: {message}\n\nReply naturally.",
                system=system,
                model_tier="fast",
                max_tokens=600,
                force_provider="ollama",
            )
        except Exception as exc:
            return Response(
                text=f"Local Ollama unavailable: {exc}",
                intent=Intent.CHAT,
                metadata={"private": True, "error": str(exc)},
            )
        return Response(text=text, intent=Intent.CHAT, metadata={"private": True})

    # --- Helpers -------------------------------------------------------
    @staticmethod
    def _is_meaningful(extraction: Any) -> bool:
        return bool(
            extraction.projects
            or extraction.work_experience
            or extraction.education
            or len(extraction.skills) >= 2
            or extraction.user_profile_updates
        )

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
            lines.append(f"  - Idea: {(i.get('content') or '')[:80]}")
        return "\n".join(lines) if lines else "  (no new entities)"
