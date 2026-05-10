"""Central orchestrator: routes via Anthropic tool use with a regex fast-path."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from typing import TYPE_CHECKING

from src.brain.context_builder import Context, ContextBuilder
from src.brain.llm_client import LLMClient, ToolDef
from src.brain.prompts import build_system_prompt
from src.memory.observability import record_tool_call, render_preferences_for_prompt

if TYPE_CHECKING:
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


def _short_repr(value: Any, limit: int = 200) -> str:
    """Compact repr for logging tool inputs/outputs without flooding the log."""
    try:
        s = str(value)
    except Exception:
        return "<unrepr>"
    if len(s) > limit:
        s = s[:limit] + f"…[+{len(s) - limit}ch]"
    return s


# Hallucination detector: regexes that match action-claim phrasings tied to
# specific write tools. If the model's final text matches any of these but
# the corresponding tool was NOT actually called this turn, we force one
# more loop iteration that either makes the call or admits it didn't.


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
# NOTE: keep this list short. Confirmation tokens like 'si', 'ok', 'vale'
# DO NOT belong here — they're often reply-to-action signals that need the
# full agentic loop with tool access. Only obvious greetings/thanks live here.
_TRIVIAL_CHAT = re.compile(
    r"^\s*(hola|hi|hey|hello|buenos? (días|tardes|noches)|gracias|thanks|"
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

        # 1. Trivial greetings — cheap text-only path, no LLM router needed.
        if _TRIVIAL_CHAT.match(message):
            context.intent = Intent.CHAT.value
            return await self._handle_chat(
                user_id, message, context, stream_message=stream_message,
            )

        # 2. Slash commands — direct skill dispatch (instant, $0).
        if message.lstrip().startswith("/"):
            context.intent = Intent.TASK.value
            return await self._handle_task(user_id, message, context, hint=None)

        # 3. EVOLVE — explicit "teach yourself X" requests.
        for intent_t, pattern in _FASTPATH_PATTERNS:
            if intent_t == Intent.EVOLVE and pattern.search(message):
                context.intent = Intent.EVOLVE.value
                return await self._handle_evolve(user_id, message, context)

        # 4. EVERYTHING ELSE — agentic loop. The driver model has the full
        # primitive catalog (gmail_*, calendar_*, memory_*, web_*, notion_*,
        # github_*, plus compound sub-agents) and decides whether to call
        # tools, save to memory, or just chat. No more pre-classifier
        # bottleneck deciding 'this is CHAT, no tools for you'.
        context.intent = Intent.TASK.value

        # Background entity extraction. Runs independently of the agent
        # loop so persistent memory always captures structured facts
        # (projects, contacts, decisions, life events, …) — even when
        # Sonnet doesn't call memory_save explicitly. process_and_store
        # is idempotent and dedups against existing entities.
        self._spawn_background_extraction(user_id, message)

        return await self._run_agentic_loop(user_id, message, context)

    def _spawn_background_extraction(self, user_id: int, message: str) -> None:
        """Fire-and-forget: extract entities from the user's message and
        persist any new facts. Runs in parallel with the agentic loop so
        it never blocks the user-facing response. Logs a concise summary
        when meaningful entities were captured — useful for confirming
        in /tools or docker logs that memory is filling up."""
        if not message or len(message.strip()) < 12:
            return
        try:
            import asyncio

            async def _runner() -> None:
                try:
                    result = await self.memory.process_and_store(
                        user_id=user_id, message=message, context_hint="",
                    )
                except Exception as exc:
                    logger.debug("background extraction failed: %s", exc)
                    return
                summary = self._summarise_extraction_for_ack(result.extraction)
                if summary:
                    logger.info("background_extraction: %s", summary)

            asyncio.create_task(_runner())
        except Exception as exc:
            logger.debug("could not schedule background extraction: %s", exc)

    @staticmethod
    def _summarise_extraction_for_ack(extraction: Any) -> str:
        """Compact 'saved X' summary, only mentioning meaningful captures.
        Returns '' when nothing worth surfacing was extracted (saves noise)."""
        bits: list[str] = []
        for label, items in (
            ("idea", extraction.ideas),
            ("project", extraction.projects),
            ("contact", extraction.contacts),
            ("decision", extraction.decisions),
            ("achievement", extraction.achievements),
            ("life event", extraction.life_events),
            ("book", extraction.books),
            ("job", extraction.work_experience),
            ("opportunity", extraction.job_opportunities),
            ("health", extraction.health_events),
            ("quote", extraction.quotes),
        ):
            n = len(items or [])
            if n:
                bits.append(f"{n} {label}{'s' if n > 1 else ''}")
        if not bits:
            return ""
        return "💾 Saved to memory: " + ", ".join(bits)

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

    AGENTIC_MAX_STEPS = 8
    # Sonnet 4.6 — the sweet spot for an agentic personal assistant. Strong
    # at tool use + reasoning without Opus's 5× price tag. The previous
    # hallucination problems came from a fragmented per-turn loop, not from
    # model capability — they're fixed by the new persistent
    # ConversationStore where Sonnet sees its own prior tool_use blocks.
    AGENTIC_MODEL_TIER = "fast"

    # Heuristic: tasks that compose 3+ verbs or span multiple domains often
    # need more than the default 5 tool calls. We bump the cap to the hard
    # limit on detection so the agent doesn't bail mid-plan.
    _COMPLEX_TASK_RE = re.compile(
        r"(\b(y|and|luego|then|después|after that|tras eso)\b.*){2,}|"
        r"\b(planifica|plan my|prepárame|prepare me|investiga.+y|research.+and)\b",
        re.I | re.S,
    )

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
        logger.info(
            "agentic_loop_start: user=%d tools_available=%d msg=%s",
            user_id, len(tools), _short_repr(message, limit=120),
        )

        capabilities = self._capabilities_block()
        learned_prefs = render_preferences_for_prompt(int(user_id), min_confidence=0.4)
        prefs_block = (
            "\nLEARNED USER PREFERENCES (from observed behaviour):\n" + learned_prefs
            if learned_prefs else ""
        )

        # Inject current datetime so the model can compute relative phrases
        # ('in 2 minutes', 'tomorrow at 9am') into ISO 8601 without asking
        # the user. UTC for tool calls (memory_remind, calendar_create);
        # Madrid local for user-facing context.
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        now_utc = datetime.now(timezone.utc)
        try:
            now_local = now_utc.astimezone(ZoneInfo("Europe/Madrid"))
        except Exception:
            now_local = now_utc
        time_block = (
            f"\nCURRENT TIME:\n"
            f"- UTC: {now_utc.isoformat(timespec='seconds')}\n"
            f"- Local (Europe/Madrid): {now_local.isoformat(timespec='seconds')} "
            f"({now_local.strftime('%A %d %B %Y, %H:%M')})\n"
            f"Use UTC when passing datetimes to tools (memory_remind.due_at, "
            f"calendar_create.start/end). For user-facing text, you can use "
            f"local time naturally ('a las 17:30').\n"
        )
        system = build_system_prompt(
            user_name=context.profile.get("name"),
            living_profile=context.living_profile,
            communication_style=context.profile.get("communication_style"),
            capabilities=capabilities,
            extra=(time_block + prefs_block +
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
                "PROACTIVE REMINDERS — be a real assistant, not a notepad:\n"
                "When the user mentions a future obligation, intent, or "
                "anxiety about forgetting something, AUTO-SCHEDULE a reminder "
                "via memory_remind without asking for permission first. The "
                "user shouldn't have to remember to ask. After scheduling, "
                "tell them in one line ('Te aviso el martes a las 10, dime "
                "si quieres cambiarlo') so they can adjust.\n"
                "Trigger examples:\n"
                "- 'tengo que llamar al banco mañana' → remind tomorrow morning\n"
                "- 'antes del viernes le tengo que decir a Marc' → remind Thursday\n"
                "- 'no se me puede olvidar el médico el martes' → remind 1h before\n"
                "- 'tengo que enviar un mail importante luego' → remind in 2h\n"
                "- 'se me ha ocurrido una idea para Atlas' → save AND remind in 5 days to revisit\n"
                "- 'estoy estresado con las entregas de mayo' → remind to take a break tomorrow\n"
                "Skip auto-reminder when:\n"
                "- User explicitly says 'no me lo recuerdes' / 'no hace falta'\n"
                "- The deadline is unclear and asking would clutter the conversation\n"
                "- It's already done by the end of this turn (e.g. you just sent the email)\n"
                "When unsure about timing, pick a sensible default (next morning 9am, "
                "1h before an event, +5 days for ideas) — better to schedule and let "
                "them tweak than to not schedule.\n\n"
                "ANTI-HALLUCINATION RULES (NON-NEGOTIABLE):\n"
                "- NEVER claim an action happened unless you actually called "
                "  the matching tool in THIS turn and saw a successful "
                "  result. 'Sent', 'Enviado', 'Scheduled', 'Created', 'Saved' "
                "  → these MUST be backed by a real tool call this turn. "
                "  If the user says 'send it' / 'envíalo' / 'do it' and you "
                "  haven't called gmail_send yet, CALL IT before claiming "
                "  it's sent. No exceptions.\n"
                "- If a tool call failed (error result), report the failure "
                "  honestly. Don't paper over it.\n"
                "- When the user instructs an action like 'send it' after a "
                "  draft you composed, your next move is ALWAYS calling the "
                "  corresponding write tool — not writing a confirmation.\n\n"
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

        # Persistent conversation history (Claude.ai / ChatGPT style):
        # the model sees its OWN prior tool_use blocks and tool_results
        # across turns, so we don't need to summarise or reconstruct anything.
        # When the user types 'envíalo' two turns later, the model's previous
        # assistant message (with the draft) is right there in history.
        from src.brain.conversation_state import get_conversation_store

        store = get_conversation_store()
        is_fresh = store.is_fresh_session(int(user_id))

        # Only on a fresh session (after >6h of inactivity) do we prepend a
        # context block — for the rest of the conversation, the message
        # history itself is the context.
        if is_fresh:
            grounding = (
                f"[Session start. Memory snapshot for grounding:]\n"
                f"{context.to_prompt_block()[:5000]}\n\n"
                f"{message}"
            )
            store.append_user(int(user_id), grounding)
        else:
            store.append_user(int(user_id), message)

        history = store.history(int(user_id))
        skill_outputs: list[tuple[str, Any]] = []
        final_text = ""
        last_inline_buttons: list = []
        last_files: list = []

        # Tier escalation: complex compound requests get the full step budget;
        # short single-intent messages cap earlier to keep cost bounded.
        max_steps = self.AGENTIC_MAX_STEPS
        if not self._COMPLEX_TASK_RE.search(message or ""):
            max_steps = min(max_steps, 5)

        for step in range(max_steps):
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
                store.append_assistant(int(user_id), result.raw_content)
                break

            if not result.tool_calls:
                final_text = result.text or "(no response)"
                store.append_assistant(int(user_id), result.raw_content)
                break

            # Persist the assistant turn (with tool_use blocks) so the next
            # iteration AND future user messages can see what was called.
            store.append_assistant(int(user_id), result.raw_content)

            tool_results: list[dict[str, Any]] = []
            for call in result.tool_calls:
                logger.info(
                    "agentic_tool_call: step=%d tool=%s args=%s",
                    step, call.name, _short_repr(call.input),
                )
                tool = primitives_by_name.get(call.name)
                if tool is None:
                    logger.warning("agentic_tool_call: UNKNOWN tool %s", call.name)
                    record_tool_call(
                        user_id=user_id, tool_name=call.name,
                        args=call.input or {}, ok=False, latency_ms=None,
                        result_preview=None, error=f"unknown tool: {call.name}",
                        turn_message=message,
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": f"(unknown tool: {call.name})",
                        "is_error": True,
                    })
                    continue
                t0 = time.monotonic()
                err: Exception | None = None
                out = ""
                try:
                    out = await tool.run(call.input or {}, context)
                except Exception as exc:
                    err = exc
                    logger.exception("agentic_tool_call: %s FAILED", tool.name)
                latency_ms = int((time.monotonic() - t0) * 1000)
                record_tool_call(
                    user_id=user_id, tool_name=tool.name,
                    args=call.input or {}, ok=err is None,
                    latency_ms=latency_ms,
                    result_preview=out if err is None else None,
                    error=str(err) if err else None,
                    turn_message=message,
                )
                if err is not None:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": f"Error: {err}",
                        "is_error": True,
                    })
                    continue
                logger.info(
                    "agentic_tool_call: %s OK %dms result=%s",
                    tool.name, latency_ms, _short_repr(out, limit=300),
                )
                skill_outputs.append((tool.name, out))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": (out or "(no result)")[:8000],
                })

            store.append_tool_results(int(user_id), tool_results)
            history = store.history(int(user_id))

        # If the loop ended without a final text turn, force one extra step
        # without tool access so the model HAS to interpret the results and
        # write a real user-facing answer (rather than dumping raw tool output).
        if not final_text and skill_outputs:
            try:
                store.append_user(int(user_id), (
                    "Now write the final user-facing answer using the "
                    "tool results above. Answer the user's actual "
                    "question — interpret, summarise, recommend. Don't "
                    "repeat raw tool output. Match the user's language."
                ))
                forced = await self.llm.agentic_step(
                    messages=store.history(int(user_id)),
                    tools=[],  # no tools — must produce text
                    system=system,
                    model_tier=self.AGENTIC_MODEL_TIER,
                    max_tokens=900,
                )
                final_text = forced.text or ""
                if final_text:
                    store.append_assistant(int(user_id), forced.raw_content)
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
