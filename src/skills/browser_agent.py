"""Vision-driven browser automation.

Loop:
  1. Take a screenshot + dump interactive elements
  2. Ask Claude (vision) what next action best advances the goal
  3. If action is irreversible (submit, payment, login), STOP and ask the user
  4. Otherwise execute and repeat
  5. Hard cap on number of steps so a misbehaving model can't burn through tokens

The skill is async-iterator-friendly: the Telegram handler that owns it
streams every step (screenshot + reasoning + decision) to the user.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from src.config import settings
from src.integrations.browser import Browser, PAYMENT_PATTERNS
from src.skills.base import BaseSkill, SkillResponse

if TYPE_CHECKING:
    from src.brain.context_builder import Context
    from src.brain.llm_client import LLMClient
    from src.memory.manager import MemoryManager


logger = logging.getLogger(__name__)


PLAN_PROMPT = """You are operating a real web browser to advance the user's goal.
Each turn you receive a screenshot, the current URL, the page title, a text excerpt
and a list of visible interactive elements. Reply with EXACTLY ONE next action.

Goal:
{goal}

Steps already taken:
{history}

Current page:
- URL: {url}
- Title: {title}
- Text excerpt: {excerpt}
- Looks like a payment / checkout page: {is_payment}

Visible elements (truncated):
{elements}

Respond ONLY with valid JSON:
{{
  "reasoning": "<short, what you observe and why this action>",
  "action": "goto" | "click" | "fill" | "press" | "scroll" | "wait" | "ask_user" | "done",
  "selector": "<CSS selector | text | label | role:name>"   // omit for goto/scroll/wait/done/ask_user
  "url": "<https://...>"                                    // only for goto
  "value": "<text>"                                          // only for fill
  "key": "Enter|Tab|Escape"                                  // only for press
  "needs_confirmation": true | false,
  "user_message": "<question to send the user>"              // only when action=ask_user
}}

Rules:
- Use `ask_user` whenever you reach a login form, a payment / checkout, a destructive
  action, or anytime you genuinely need a human decision.
- Never guess credit-card numbers, passwords, or one-time codes.
- Set `needs_confirmation: true` for any submit-like action (form submit, "buy",
  "send", "delete", "confirm").
- Prefer text/label-based selectors over brittle CSS.
- `done` when the goal is met, with `reasoning` summarising the outcome.
"""


@dataclass
class AgentStep:
    action: str
    reasoning: str = ""
    url: Optional[str] = None
    selector: Optional[str] = None
    value: Optional[str] = None
    key: Optional[str] = None
    needs_confirmation: bool = False
    user_message: Optional[str] = None
    screenshot_path: Optional[Path] = None
    page_url: str = ""
    page_title: str = ""


@dataclass
class AgentEvent:
    """Streamed back to the Telegram handler turn by turn."""
    kind: str  # "step" | "needs_confirmation" | "done" | "error"
    text: str = ""
    screenshot: Optional[Path] = None
    step: Optional[AgentStep] = None


class BrowserAgentSkill(BaseSkill):
    name = "browser_agent"
    description = (
        "Drive a real Chromium browser to advance a goal — fill forms, click links, "
        "search sites — pausing for user confirmation before any irreversible action."
    )
    triggers = [
        "/browse", "/browser", "browse the web for me",
        "fill this form", "navega por", "rellena el formulario",
    ]

    MAX_STEPS = 12

    def __init__(self, llm: "LLMClient", memory: "MemoryManager") -> None:
        self.llm = llm
        self.memory = memory

    def can_handle(self, message: str, intent: str | None = None) -> float:
        m = message.lower().strip()
        if m.startswith(("/browse", "/browser")):
            return 0.95
        if any(t in m for t in (
            "fill this form", "navega por", "rellena el formulario", "browse for me"
        )):
            return 0.85
        return super().can_handle(message, intent)

    async def execute(self, message: str, context: "Context") -> SkillResponse:
        # Default fallback: not used directly; the Telegram handler calls
        # `iter_actions(...)` to stream events. This synchronous-style execute
        # only runs through if the skill is dispatched outside the bot loop
        # (e.g. from the agentic planner).
        events: list[AgentEvent] = []
        async for ev in self.iter_actions(message, context):
            events.append(ev)
        text_parts = [e.text for e in events if e.text]
        return SkillResponse(text="\n\n".join(text_parts) or "(no events)")

    async def iter_actions(
        self,
        message: str,
        context: "Context",
        max_steps: int | None = None,
    ) -> AsyncIterator[AgentEvent]:
        goal = self._strip_command(message) or "(no goal given)"
        max_steps = max_steps or self.MAX_STEPS

        browser = Browser(headless=True)
        screenshots_dir = settings.uploads_dir / "browser"
        history: list[AgentStep] = []

        try:
            await browser.start()
        except Exception as exc:
            yield AgentEvent(kind="error", text=f"Could not start browser: {exc}")
            return

        try:
            for step_index in range(1, max_steps + 1):
                state = await browser.state(screenshots_dir)
                step = await self._decide_next(goal, history, state, browser)
                step.screenshot_path = state.screenshot_path
                step.page_url = state.url
                step.page_title = state.title

                history.append(step)

                if step.action == "done":
                    yield AgentEvent(
                        kind="done",
                        text=f"✅ {step.reasoning}",
                        screenshot=state.screenshot_path,
                        step=step,
                    )
                    return

                if step.action == "ask_user" or step.needs_confirmation or state.looks_like_payment:
                    yield AgentEvent(
                        kind="needs_confirmation",
                        text=(step.user_message or step.reasoning
                              or "Need your confirmation before continuing."),
                        screenshot=state.screenshot_path,
                        step=step,
                    )
                    return

                # Stream the planned step BEFORE executing so the user sees
                # exactly what is about to happen with an inline-cancel option.
                yield AgentEvent(
                    kind="step",
                    text=self._format_step(step_index, step),
                    screenshot=state.screenshot_path,
                    step=step,
                )

                ok = await self._execute_step(browser, step)
                if not ok:
                    yield AgentEvent(
                        kind="error",
                        text=f"Step {step_index} failed: {step.action} {step.selector or step.url}",
                        step=step,
                    )
                    return

            yield AgentEvent(
                kind="error",
                text=f"Max steps ({max_steps}) reached without finishing.",
            )
        finally:
            await browser.close()

    # --- Internals ------------------------------------------------------
    # Vision-capable models: Sonnet for default routing (cheaper), Opus only
    # if the user explicitly opts in via env. Both support image inputs.
    DEFAULT_MODEL_TIER = "fast"

    async def _decide_next(
        self,
        goal: str,
        history: list[AgentStep],
        state,  # PageState, but avoid circular import in annotation
        browser: Browser,
    ) -> AgentStep:
        elements_block = json.dumps(state.interactive_elements[:30], indent=0)[:2000]
        history_block = "\n".join(
            f"{i+1}. {h.action} {h.selector or h.url or ''} → {h.reasoning[:120]}"
            for i, h in enumerate(history[-6:])
        ) or "(none)"

        prompt = PLAN_PROMPT.format(
            goal=goal,
            history=history_block,
            url=state.url,
            title=state.title,
            excerpt=state.text_excerpt[:1500],
            is_payment=state.looks_like_payment,
            elements=elements_block,
        )

        # Vision: include the latest screenshot when available.
        try:
            raw = await self._call_with_vision(state.screenshot_path, prompt)
        except Exception as exc:
            logger.warning("Vision call failed (%s) — falling back to text-only", exc)
            raw = await self.llm.complete(
                prompt=prompt,
                system="You drive a browser. Reply with strict JSON only.",
                model_tier=self.DEFAULT_MODEL_TIER,
                max_tokens=600,
                temperature=0.0,
            )

        data = self._parse_json(raw)
        if not data:
            return AgentStep(action="ask_user", reasoning="Couldn't decide next step",
                              user_message="Algo salió raro al razonar. ¿Sigo, o cancelamos?")

        return AgentStep(
            action=str(data.get("action", "ask_user")).lower(),
            reasoning=str(data.get("reasoning") or "")[:500],
            url=data.get("url"),
            selector=data.get("selector"),
            value=data.get("value"),
            key=data.get("key"),
            needs_confirmation=bool(data.get("needs_confirmation", False)),
            user_message=data.get("user_message"),
        )

    async def _call_with_vision(self, screenshot: Path | None, prompt: str) -> str:
        # Default to Sonnet — has vision and is ~5× cheaper than Opus.
        model = (
            self.llm.model_powerful
            if self.DEFAULT_MODEL_TIER == "powerful"
            else self.llm.model_fast
        )
        if screenshot is None or not screenshot.exists():
            return await self.llm.complete(
                prompt=prompt,
                system="You drive a browser. Reply with strict JSON only.",
                model_tier=self.DEFAULT_MODEL_TIER,
                max_tokens=800,
                temperature=0.0,
            )

        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        data = base64.standard_b64encode(screenshot.read_bytes()).decode()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 800,
            "system": "You drive a browser. Reply with strict JSON only.",
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": data},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        if self.llm._supports_temperature(model):
            kwargs["temperature"] = 0.0
        resp = await client.messages.create(**kwargs)
        if hasattr(resp, "usage"):
            self.llm._record_usage(model, resp.usage)
        return "".join(b.text for b in resp.content if hasattr(b, "text"))

    async def _execute_step(self, browser: Browser, step: AgentStep) -> bool:
        try:
            if step.action == "goto" and step.url:
                await browser.goto(step.url)
                return True
            if step.action == "click" and step.selector:
                # Try multiple selector strategies.
                for tactic in (browser.click_text, browser.click_role_wrapper(step.selector), browser.click_css):
                    if await tactic(step.selector):
                        return True
                return False
            if step.action == "fill" and step.selector:
                if await browser.fill(step.selector, step.value or ""):
                    return True
                return await browser.fill_label(step.selector, step.value or "")
            if step.action == "press" and step.key:
                await browser.press(step.key)
                return True
            if step.action == "scroll":
                await browser.scroll()
                return True
            if step.action == "wait":
                await browser.wait(2.0)
                return True
        except Exception as exc:
            logger.warning("Step execution error: %s", exc)
        return False

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        s = raw.strip()
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
        if m:
            s = m.group(1)
        try:
            return json.loads(s)
        except Exception as exc:
            logger.warning("Could not parse browser-agent JSON: %s\nraw=%s", exc, raw[:300])
            return None

    @staticmethod
    def _format_step(idx: int, step: AgentStep) -> str:
        target = step.url or step.selector or step.key or ""
        return (
            f"🌐 Step {idx} — `{step.action}` {target}\n"
            f"_{step.reasoning[:300]}_"
        )

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        for tok in ("/browse", "/browser"):
            if m.lower().startswith(tok):
                return m[len(tok):].strip()
        return m


# --- Browser convenience wrappers ------------------------------------------
# These extend Browser with multiple-strategy click/fill helpers used above.

async def _click_role(self, name: str, timeout: float = 5000) -> bool:
    for role in ("button", "link", "menuitem"):
        try:
            await self._page.get_by_role(role, name=name).first.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _click_css(self, selector: str, timeout: float = 5000) -> bool:
    try:
        await self._page.locator(selector).first.click(timeout=timeout)
        return True
    except Exception:
        return False


def click_role_wrapper(self, selector):
    """Returns an async callable to fit the click-tactic interface."""
    async def _do(arg):
        return await _click_role(self, selector)
    return _do


Browser.click_role_wrapper = click_role_wrapper  # type: ignore[attr-defined]
Browser.click_css = _click_css  # type: ignore[attr-defined]
