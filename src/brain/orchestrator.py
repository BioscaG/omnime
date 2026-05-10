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
#
# The TASK tool's description and skill enum are populated dynamically from
# the live skill registry (see ``Orchestrator._build_routing_tools``). This
# means the routing LLM sees the actual list of available capabilities — so
# natural-language requests like "mira mi email" or "tradúceme esto" get
# matched to the right skill without needing a slash command.

_STATIC_ROUTING_TOOLS: list[ToolDef] = [
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


def _build_task_tool(skills_catalog: str, skill_names: list[str]) -> ToolDef:
    return ToolDef(
        name="TASK",
        description=(
            "Run one of the user's available capabilities. Pick TASK whenever "
            "the user is requesting an ACTION that matches one of the skills "
            "below (even if they phrase it casually, in any language, without "
            "a slash command). Available skills:\n"
            + (skills_catalog or "(none registered yet)")
        ),
        input_schema={
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Exact name of the skill to run.",
                    **({"enum": skill_names} if skill_names else {}),
                }
            },
            "required": ["skill"] if skill_names else [],
        },
    )


# Backwards-compat: tests import ROUTING_TOOLS by name. Keep a stable list
# even if the live one is rebuilt per-request.
ROUTING_TOOLS: list[ToolDef] = _STATIC_ROUTING_TOOLS + [
    _build_task_tool("(populated at runtime from skill registry)", []),
]


# Quick regex fast-path: skip the LLM for obvious cases.
_FASTPATH_PATTERNS: list[tuple[Intent, re.Pattern[str]]] = [
    (Intent.EVOLVE, re.compile(r"^\s*evolve[:\s]", re.I)),
    (Intent.EVOLVE, re.compile(r"\b(add the ability|teach yourself|new skill|learn how)\b", re.I)),
    (Intent.TASK, re.compile(r"^/(cv|cv_for|email|inbox|mail|read|search_mail|scheduled_emails|briefing|research|review|code|fetch|url|scrape|browse)\b", re.I)),
    (Intent.TASK, re.compile(r"^https?://", re.I)),
    # Natural-language URL share: "aquí tienes mi web https://...", etc.
    (Intent.TASK, re.compile(r"\b(https?://|www\.)\S+\.\S+", re.I)),
    (Intent.QUERY, re.compile(r"^\s*(what|who|when|where|which|how many) (did|do|are|is|was) i\b", re.I)),
    (Intent.QUERY, re.compile(r"^\s*(qué|quién|cuándo|dónde|cuáles|cuántos) (hice|tengo|son|fue|trabajé)\b", re.I)),
    (Intent.QUERY, re.compile(r"^/search\b", re.I)),
]


# Trivial chat patterns — single words, greetings, acknowledgements. We dispatch
# these straight to CHAT to skip the routing LLM call entirely.
_TRIVIAL_CHAT = re.compile(
    r"^\s*(hola|hi|hey|hello|buenos? (días|tardes|noches)|gracias|thanks|ok|vale|si|sí|no|"
    r"jaja|jeje|lol|👋|😀|🙂)\W*$",
    re.I,
)


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

    def _live_routing_tools(self) -> list[ToolDef]:
        """Build the routing tools with the TASK tool listing the actual
        skills currently registered. Falls back to the static placeholder if
        no registry was wired in (e.g. unit tests for routing only)."""
        if self.skill_registry is None:
            return ROUTING_TOOLS
        catalog = self.skill_registry.as_catalog()
        names = self.skill_registry.enabled_names()
        return _STATIC_ROUTING_TOOLS + [_build_task_tool(catalog, names)]

    async def classify_intent(
        self,
        message: str,
        context: Context | None = None,
    ) -> tuple[Intent, dict[str, Any]]:
        # 1. Trivial greetings / acknowledgements go straight to CHAT.
        if _TRIVIAL_CHAT.match(message):
            return Intent.CHAT, {"source": "trivial"}

        # 2. Regex fast-path for obvious slash commands and patterns.
        for intent, pattern in _FASTPATH_PATTERNS:
            if pattern.search(message):
                return intent, {"source": "regex"}

        # 3. Tool-use routing — uses the cheap "tiny" tier (Haiku) by default.
        ctx_block = context.to_prompt_block() if context else "(none)"
        try:
            result = await self.llm.use_tools(
                prompt=(
                    "Pick the right tool to handle this user message. If the "
                    "user is asking you to DO something that matches one of "
                    "the TASK skills, choose TASK and set the `skill` field "
                    "to the matching skill name.\n\n"
                    f"Recent context:\n{ctx_block[:1500]}\n\n"
                    f'User message: "{message}"'
                ),
                tools=self._live_routing_tools(),
                system="You route messages by selecting exactly one tool.",
                model_tier="tiny",
                max_tokens=150,
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

    def _capabilities_block(self) -> str | None:
        if self.skill_registry is None:
            return None
        try:
            return self.skill_registry.as_catalog() or None
        except Exception as exc:
            logger.debug("capabilities catalog failed: %s", exc)
            return None

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
            capabilities=self._capabilities_block(),
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

        # Slash-command short-circuit ONLY: when the user typed `/inbox`,
        # `/cv`, etc., dispatch the matching skill directly so we get instant
        # zero-cost responses with the skill's rich UI. ANY other message
        # (natural language) goes through the agentic loop so the model can
        # combine primitives and decide what to do — that's where the magic
        # happens. We DON'T short-circuit on the classifier's `skill` hint
        # for non-slash messages anymore: doing that bypassed the loop and
        # made "mira mis mails" still return the pre-cooked email_inbox UI.
        if message.lstrip().startswith("/"):
            skill = None
            if hint and hint.get("skill"):
                skill = self.skill_registry.get(hint["skill"])
            if skill is None:
                skill = self.skill_registry.find_best_skill(message, context)
            if skill is not None:
                sr = await skill.execute(message=message, context=context)
                return Response(
                    text=sr.text,
                    intent=Intent.TASK,
                    inline_buttons=sr.inline_buttons,
                    files=sr.files,
                    metadata={"skill": skill.name, **sr.metadata},
                )

        # Natural-language TASK → agentic primitives loop.
        return await self._run_agentic_loop(user_id, message, context)

    AGENTIC_MAX_STEPS = 5
    AGENTIC_MODEL_TIER = "fast"  # Sonnet 4.6 — strong reasoning without Opus cost

    async def _run_agentic_loop(
        self,
        user_id: int,
        message: str,
        context: Context,
    ) -> Response:
        """Pure-primitives agentic loop: every capability — atomic data
        primitive or compound sub-agent — is exposed as a single tool. The
        driver model decides what to call, in what order, and writes the
        final answer itself. No skill-level pre-rendering interferes."""
        from src.tools import collect_default_tools, tool_to_def

        primitive_tools = collect_default_tools(skill_registry=self.skill_registry)
        primitives_by_name = {t.name: t for t in primitive_tools}
        tools = [tool_to_def(t) for t in primitive_tools]
        if not tools:
            return await self._handle_chat(user_id, message, context)

        capabilities = self._capabilities_block()
        system = build_system_prompt(
            user_name=context.profile.get("name"),
            living_profile=context.living_profile,
            communication_style=context.profile.get("communication_style"),
            capabilities=capabilities,
            extra=(
                "\nYou are operating as a fully agentic personal assistant. "
                "Every capability is exposed to you as a TOOL primitive — "
                "Gmail CRUD (gmail_list, gmail_read, gmail_send, ...), memory "
                "(memory_search, memory_save, memory_recall_profile), web "
                "(web_fetch, web_search), and compound sub-agents (browser, "
                "cv generator, etc.). YOU choose what to call, in what "
                "order, and how to compose the response.\n\n"
                "PHILOSOPHY:\n"
                "- Tools return raw JSON data. YOU interpret it.\n"
                "- Compose freely: 'check inbox + reply to one + save the "
                "  outcome to memory' is three tool calls in one turn.\n"
                "- Be ambitious. If a question would benefit from looking "
                "  something up, look it up. If you should remember "
                "  something the user said, save it. Don't ask permission "
                "  for read-only actions; just do them.\n"
                "- Be conservative on writes that have external impact "
                "  (gmail_send): default delay_minutes is 10 so the user "
                "  can cancel; honour that unless they explicitly say "
                "  'send now'.\n\n"
                "FINAL-ANSWER RULES:\n"
                "1. NEVER end without a final text turn. After tool calls, "
                "   write the user-facing answer.\n"
                "2. Answer the user's actual question — DON'T dump raw "
                "   tool output. Examples:\n"
                "   - 'tengo algún mail importante?' → look at gmail_list "
                "     JSON, filter by importance yourself, answer in 1-2 "
                "     lines: 'Sí, el de Anthropic sobre la factura. Los "
                "     demás son newsletters.'\n"
                "   - 'lee el de X y respóndele que voy mañana' → "
                "     gmail_list → gmail_read → gmail_send (with 10-min "
                "     delay). Confirm what you did, don't re-print bodies.\n"
                "   - 'qué decidí sobre Y?' → memory_search, then answer "
                "     using the recalled facts.\n"
                "3. Match the user's language (Spanish or English) in the "
                "   final answer.\n"
                f"Hard cap: {self.AGENTIC_MAX_STEPS} tool calls per turn."
            ),
        )

        history: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Context (recent activity):\n{context.to_prompt_block()[:1500]}\n\n"
                    f"User message:\n{message}"
                ),
            },
        ]
        skill_outputs: list[tuple[str, Any]] = []
        final_text = ""
        last_inline_buttons: list = []
        last_files: list = []

        for step in range(self.AGENTIC_MAX_STEPS):
            try:
                result = await self.llm.agentic_step(
                    messages=history,
                    tools=tools,
                    system=system,
                    model_tier=self.AGENTIC_MODEL_TIER,
                    max_tokens=1500,
                )
            except Exception as exc:
                logger.warning("agentic step %s failed: %s", step, exc)
                final_text = f"⚠️ Agent loop failed: {exc}"
                break

            if result.text and not result.tool_calls:
                final_text = result.text
                break

            if not result.tool_calls:
                final_text = result.text or "(no response)"
                break

            # Round-trip the assistant turn so subsequent turns can match
            # tool_use IDs to tool_result IDs.
            history.append({"role": "assistant", "content": result.raw_content})

            tool_results: list[dict[str, Any]] = []
            for call in result.tool_calls:
                tool = primitives_by_name.get(call.name)
                if tool is None:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": f"(unknown tool: {call.name})",
                        "is_error": True,
                    })
                    continue
                try:
                    out = await tool.run(call.input or {}, context)
                except Exception as exc:
                    logger.exception("tool %s failed in agentic loop", tool.name)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": f"Error: {exc}",
                        "is_error": True,
                    })
                    continue
                skill_outputs.append((tool.name, out))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": (out or "(no result)")[:8000],
                })

            history.append({"role": "user", "content": tool_results})

        # If the loop ended without a final text turn, force one extra step
        # without tool access so the model HAS to interpret the results and
        # write a real user-facing answer (rather than dumping raw tool output).
        if not final_text and skill_outputs:
            try:
                history.append({
                    "role": "user",
                    "content": (
                        "Now write the final user-facing answer using the "
                        "tool results above. Answer the user's actual "
                        "question — interpret, summarise, recommend. Don't "
                        "repeat raw tool output. Match the user's language."
                    ),
                })
                forced = await self.llm.agentic_step(
                    messages=history,
                    tools=[],  # no tools — must produce text
                    system=system,
                    model_tier=self.AGENTIC_MODEL_TIER,
                    max_tokens=900,
                )
                final_text = forced.text or ""
            except Exception as exc:
                logger.warning("forced final-answer step failed: %s", exc)

        if not final_text:
            final_text = "I tried but didn't produce anything useful — try rephrasing?"

        # Side-effects raised by tools during the loop: inline buttons, files,
        # scheduled-send cancellation hooks. Surface them on the Response so
        # the bot UI can render them.
        side = getattr(context, "_tool_side_effects", None) or {}
        for row in side.get("inline_buttons", []) or []:
            last_inline_buttons = (last_inline_buttons or []) + [row]
        for f in side.get("files", []) or []:
            last_files = (last_files or []) + [f]
        for sched in side.get("scheduled_sends", []):
            cancel_row = [{
                "text": f"❌ Cancel send → {sched.get('to')}",
                "callback_data": f"email:scheduled_cancel:{sched['send_id']}",
            }]
            last_inline_buttons = (last_inline_buttons or []) + [cancel_row]

        return Response(
            text=final_text,
            intent=Intent.TASK,
            inline_buttons=last_inline_buttons,
            files=last_files,
            metadata={
                "agentic": True,
                "steps": len(skill_outputs),
                "tools_called": [name for name, _ in skill_outputs],
            },
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
            capabilities=self._capabilities_block(),
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
        from src.config import settings as _settings

        if (
            stream_message is None
            or self.llm.provider != "anthropic"
            or not _settings.enable_streaming
        ):
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
            capabilities=self._capabilities_block(),
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
