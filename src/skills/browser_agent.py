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
from src.skills.browser_memory import BrowserMemory, domain_of, render_priming

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

{prior_knowledge}

Steps already taken (with outcome):
{history}

Selectors that just FAILED on this site (don't repeat them):
{blacklist}

Current page:
- URL: {url}
- Title: {title}
- Text excerpt: {excerpt}
- Looks like a payment / checkout page: {is_payment}
- Looks like a captcha / anti-bot page: {is_blocked}

Visible elements (truncated):
{elements}

Respond ONLY with valid JSON:
{{
  "reasoning": "<short, what you observe and why this action>",
  "action": "goto" | "click" | "fill" | "type" | "press" | "scroll" | "wait" | "ask_user" | "done",
  "selector_type": "css" | "text" | "role" | "label",   // REQUIRED for click/fill/type
  "selector": "<the locator value, see selector_type below>",
  "url": "<https://...>",                                // only for goto
  "value": "<text>",                                     // only for fill / type
  "key": "Enter|Tab|Escape",                             // only for press
  "needs_confirmation": true | false,
  "user_message": "<question to send the user>"           // only when action=ask_user
}}

Action notes:
- If the URL is `about:blank`, you are at the very start. Your first action
  MUST be a `goto` to the most appropriate target site for the goal.
- `fill`: clears the field then sets the exact value. Best for plain text inputs.
- `type`: focuses the field and types character-by-character (triggers autocomplete
  dropdowns properly). Use this for fields that need autocomplete (Renfe, airline
  origin/destination, etc.).
- `press`: sends a keyboard key globally (e.g. Enter to submit a form).
- After dismissing a cookie banner, do NOT click it again — it is already gone
  and the next screenshot will show the real page.

Selector types — pick ONE and put the bare value in `selector`:
- `css`: a real CSS selector, e.g. `input[name='q']`, `#submit`, `.btn-primary`
- `text`: visible text on the element, e.g. `Rechazar todo` (no `text=` prefix)
- `role`: ARIA role + name, e.g. `button:Aceptar`  (format `<role>:<accessible name>`)
- `label`: the form label associated with an input, e.g. `Origen`

Rules:
- Use `ask_user` whenever you reach a login form, a payment / checkout, a destructive
  action, or anytime you genuinely need a human decision.
- Never guess credit-card numbers, passwords, or one-time codes.
- Set `needs_confirmation: true` for any submit-like action (form submit, "buy",
  "send", "delete", "confirm").
- If the previous attempt failed, try a DIFFERENT selector type or strategy. Do not
  repeat the same selector that just failed.
- If the page looks blocked (captcha, "unusual traffic"), use `ask_user` and explain.
- `done` when the goal is met, with `reasoning` summarising the outcome.
"""


@dataclass
class AgentStep:
    action: str
    reasoning: str = ""
    url: Optional[str] = None
    selector: Optional[str] = None
    selector_type: Optional[str] = None
    value: Optional[str] = None
    key: Optional[str] = None
    needs_confirmation: bool = False
    user_message: Optional[str] = None
    screenshot_path: Optional[Path] = None
    page_url: str = ""
    page_title: str = ""
    succeeded: Optional[bool] = None


def _compress_image(path: Path, max_width: int = 1024, quality: int = 60) -> tuple[bytes, str]:
    """Resize + JPEG-compress to keep Anthropic image tokens minimal."""
    try:
        from PIL import Image
        import io as _io

        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, max(1, int(img.height * ratio)))
                img = img.resize(new_size, Image.LANCZOS)
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception as exc:
        logger.warning("image compression failed (%s) — sending raw", exc)
        return path.read_bytes(), "image/png"


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

    MAX_STEPS = 20

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
        logger.info("iter_actions: goal=%r max_steps=%d", goal, max_steps)

        browser = Browser(headless=True)
        screenshots_dir = settings.uploads_dir / "browser"
        history: list[AgentStep] = []

        try:
            logger.info("iter_actions: starting browser")
            await browser.start()
            logger.info("iter_actions: browser started, page is about:blank")
            # Don't pre-navigate. Let the agent's first action be a `goto` to
            # the right destination — saves 2-3 wasted steps fighting cookie
            # dialogs on landing pages we never wanted in the first place.
        except Exception as exc:
            logger.exception("iter_actions: browser.start() failed")
            yield AgentEvent(kind="error", text=f"Could not start browser: {exc}")
            return

        # Per-session blacklist of selectors that just failed — fed back to
        # the LLM each turn so it doesn't repeat the same mistake.
        failed_selectors: list[str] = []

        try:
            for step_index in range(1, max_steps + 1):
                logger.info("iter_actions: step %d — capturing state", step_index)
                state = await browser.state(screenshots_dir)
                logger.info("iter_actions: state url=%s title=%s", state.url, state.title)
                step = await self._decide_next(
                    goal=goal,
                    history=history,
                    state=state,
                    browser=browser,
                    user_id=context.user_id,
                    failed_selectors=failed_selectors,
                )
                logger.info(
                    "iter_actions: decided action=%s selector=%r needs_conf=%s",
                    step.action, step.selector, step.needs_confirmation,
                )
                step.screenshot_path = state.screenshot_path
                step.page_url = state.url
                step.page_title = state.title

                history.append(step)

                if step.action == "done":
                    # Persist what worked so future sessions learn from this.
                    try:
                        await self._learn_from_session(
                            user_id=context.user_id,
                            goal=goal,
                            history=history,
                            final_url=state.url,
                        )
                    except Exception as exc:
                        logger.warning("Could not save recipe: %s", exc)
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
                step.succeeded = ok
                if not ok:
                    # Track failed selector so we don't repeat it next turn.
                    if step.selector:
                        tag = f"{step.selector_type or '?'}={step.selector}"
                        if tag not in failed_selectors:
                            failed_selectors.append(tag)
                    logger.info(
                        "iter_actions: step %d failed, will let model retry",
                        step_index,
                    )
                    yield AgentEvent(
                        kind="step",
                        text=f"⚠️ Step {step_index} failed; the agent will adapt.",
                        step=step,
                    )

            yield AgentEvent(
                kind="error",
                text=f"Max steps ({max_steps}) reached without finishing.",
            )
        finally:
            await browser.close()

    # --- Internals ------------------------------------------------------
    @property
    def DEFAULT_MODEL_TIER(self) -> str:
        # Configurable via BROWSER_MODEL_TIER env. Default: tiny (Haiku 4.5).
        return getattr(settings, "browser_model_tier", "tiny") or "tiny"

    async def _decide_next(
        self,
        goal: str,
        history: list[AgentStep],
        state,  # PageState, but avoid circular import in annotation
        browser: Browser,
        user_id: int | None = None,
        failed_selectors: list[str] | None = None,
    ) -> AgentStep:
        # Smaller context = cheaper. 18 elements + last 4 steps is enough.
        elements_block = json.dumps(state.interactive_elements[:18], indent=0)[:1200]
        history_block = "\n".join(
            f"{i+1}. {h.action} {h.selector_type or ''}={h.selector or h.url or ''} "
            f"→ {'OK' if h.succeeded else 'FAILED' if h.succeeded is False else '?'}: "
            f"{h.reasoning[:100]}"
            for i, h in enumerate(history[-4:])
        ) or "(none)"

        is_blocked = bool(re.search(
            r"/sorry/|recaptcha|unusual traffic|are you (a )?human|please verify",
            (state.url + " " + state.text_excerpt).lower(),
        ))

        # Pull learnt recipes + notes for this domain.
        prior_knowledge_block = ""
        if user_id is not None:
            dom = domain_of(state.url)
            if dom:
                recipes = BrowserMemory.find_recipes(user_id, dom, limit=2)
                notes = BrowserMemory.get_notes(user_id, dom, limit=6)
                prior_knowledge_block = render_priming(recipes, notes) or ""

        blacklist = (
            "\n".join(f"- {s}" for s in (failed_selectors or [])[-10:])
            or "(none)"
        )

        prompt = PLAN_PROMPT.format(
            goal=goal,
            prior_knowledge=prior_knowledge_block or "(no prior knowledge for this site)",
            history=history_block,
            blacklist=blacklist,
            url=state.url,
            title=state.title,
            excerpt=state.text_excerpt[:800],
            is_payment=state.looks_like_payment,
            is_blocked=is_blocked,
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

        sel = data.get("selector")
        sel_type = data.get("selector_type")
        # Normalise legacy formats Claude sometimes emits despite instructions.
        if sel and not sel_type:
            sel_type, sel = _infer_selector_type(sel)
        return AgentStep(
            action=str(data.get("action", "ask_user")).lower(),
            reasoning=str(data.get("reasoning") or "")[:500],
            url=data.get("url"),
            selector=sel,
            selector_type=sel_type,
            value=data.get("value"),
            key=data.get("key"),
            needs_confirmation=bool(data.get("needs_confirmation", False)),
            user_message=data.get("user_message"),
        )

    async def _call_with_vision(self, screenshot: Path | None, prompt: str) -> str:
        tier = self.DEFAULT_MODEL_TIER
        model = self.llm._model_for(tier)
        if screenshot is None or not screenshot.exists():
            return await self.llm.complete(
                prompt=prompt,
                system="You drive a browser. Reply with strict JSON only.",
                model_tier=tier,
                max_tokens=600,
                temperature=0.0,
            )

        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        # Resize + JPEG-compress to slash image-token cost (~2-3× cheaper).
        img_bytes, media_type = _compress_image(
            screenshot,
            max_width=settings.browser_screenshot_width,
            quality=settings.browser_screenshot_quality,
        )
        data = base64.standard_b64encode(img_bytes).decode()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 600,
            "system": "You drive a browser. Reply with strict JSON only.",
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
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
                ok = await _click_by_type(browser, step.selector_type, step.selector)
                if ok:
                    # Let UI animations / dropdowns settle before next screenshot.
                    await browser.wait(1.5)
                return ok
            if step.action == "fill" and step.selector:
                ok = await _fill_by_type(
                    browser, step.selector_type, step.selector, step.value or ""
                )
                if ok:
                    await browser.wait(1.0)
                return ok
            if step.action == "type" and step.selector:
                ok = await _type_by_type(
                    browser, step.selector_type, step.selector, step.value or ""
                )
                if ok:
                    # Slow type triggers autocomplete; wait for dropdown to populate.
                    await browser.wait(1.8)
                return ok
            if step.action == "press" and step.key:
                await browser.press(step.key)
                await browser.wait(1.5)
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

    async def _learn_from_session(
        self,
        user_id: int,
        goal: str,
        history: list[AgentStep],
        final_url: str,
    ) -> None:
        """Persist the successful step sequence + ask the LLM for site notes."""
        # Recipe = the successful steps for the domain we ended on.
        domain = domain_of(final_url)
        if not domain:
            # Fall back to the first non-blank URL in history.
            for h in history:
                d = domain_of(h.page_url)
                if d:
                    domain = d
                    break
        if not domain:
            return

        successful_steps: list[dict[str, Any]] = []
        for h in history:
            if h.succeeded is False:
                continue
            successful_steps.append({
                "action": h.action,
                "selector_type": h.selector_type,
                "selector": h.selector,
                "value": h.value,
                "url": h.url,
                "key": h.key,
                "page_url": h.page_url,
                "reasoning": h.reasoning[:160],
            })
        BrowserMemory.save_recipe(user_id, domain, goal[:200], successful_steps)

        # Ask the LLM for 2-4 short observations about the site to remember.
        try:
            history_text = "\n".join(
                f"{i+1}. {h.action} {h.selector_type or ''}={h.selector or h.url or ''} "
                f"({'OK' if h.succeeded else 'FAILED' if h.succeeded is False else '?'})"
                for i, h in enumerate(history[-12:])
            )
            raw = await self.llm.complete(
                prompt=(
                    f"You just finished a browser session on {domain}.\n"
                    f"Goal: {goal}\nFinal URL: {final_url}\n\n"
                    f"Steps:\n{history_text}\n\n"
                    "Output JSON: {\"notes\": [\"<observation 1>\", ...]} — 2-4 short, "
                    "actionable notes about THIS site that would help next time "
                    "(quirks, working selectors, dropdowns that need typing not filling, "
                    "anti-bot tactics, etc.). Keep each note under 120 chars."
                ),
                system="You produce concise site-specific learnings as strict JSON.",
                model_tier="tiny",
                max_tokens=400,
                temperature=0.0,
            )
            data = self._parse_json(raw) or {}
            for note in (data.get("notes") or [])[:4]:
                if isinstance(note, str) and note.strip():
                    BrowserMemory.add_note(user_id, domain, note.strip())
        except Exception as exc:
            logger.warning("Could not generate site notes: %s", exc)

    @staticmethod
    def _strip_command(message: str) -> str:
        m = message.strip()
        for tok in ("/browse", "/browser"):
            if m.lower().startswith(tok):
                return m[len(tok):].strip()
        return m


# --- Selector dispatchers ---------------------------------------------------
# Single source of truth for translating (selector_type, selector) into a
# Playwright action. Keeps the LLM prompt simple and the failure modes
# observable.

def _infer_selector_type(value: str) -> tuple[str, str]:
    """Best-effort guess when the LLM forgets to set selector_type."""
    v = (value or "").strip()
    if v.startswith("text="):
        return "text", v[len("text="):]
    if v.startswith("label="):
        return "label", v[len("label="):]
    if v.startswith("role:") or v.startswith("role="):
        return "role", v.split(":", 1)[1] if ":" in v else v.split("=", 1)[1]
    # Heuristics: brackets/IDs/classes → CSS; anything else → text.
    if any(ch in v for ch in "[]#.>") or v.startswith((".", "#")) or "(" in v:
        return "css", v
    return "text", v


async def _click_by_type(browser: Browser, sel_type: str | None, value: str) -> bool:
    sel_type = (sel_type or "").lower() or _infer_selector_type(value)[0]
    page = browser._page
    if page is None:
        return False
    try:
        if sel_type == "css":
            await page.locator(value).first.click(timeout=6000)
            return True
        if sel_type == "text":
            await page.get_by_text(value, exact=False).first.click(timeout=6000)
            return True
        if sel_type == "role":
            role, _, name = value.partition(":")
            role = role.strip() or "button"
            name = name.strip()
            await page.get_by_role(role, name=name).first.click(timeout=6000)
            return True
        if sel_type == "label":
            await page.get_by_label(value).first.click(timeout=6000)
            return True
    except Exception as exc:
        logger.warning("click(%s=%r) failed: %s", sel_type, value, exc)
    return False


async def _type_by_type(browser: Browser, sel_type: str | None, value: str, text: str) -> bool:
    """Focus the locator then type characters one at a time (triggers
    keyup/input handlers — needed for autocomplete dropdowns)."""
    sel_type = (sel_type or "").lower() or _infer_selector_type(value)[0]
    page = browser._page
    if page is None:
        return False
    try:
        if sel_type == "css":
            locator = page.locator(value).first
        elif sel_type == "label":
            locator = page.get_by_label(value).first
        elif sel_type == "role":
            role, _, name = value.partition(":")
            locator = page.get_by_role(role.strip() or "textbox", name=name.strip()).first
        elif sel_type == "text":
            try:
                locator = page.get_by_placeholder(value).first
                await locator.click(timeout=4000)
            except Exception:
                locator = page.get_by_label(value).first
        else:
            return False
        await locator.click(timeout=6000)
        await locator.fill("")
        await page.keyboard.type(text, delay=60)
        return True
    except Exception as exc:
        logger.warning("type(%s=%r) failed: %s", sel_type, value, exc)
        return False


async def _fill_by_type(browser: Browser, sel_type: str | None, value: str, text: str) -> bool:
    sel_type = (sel_type or "").lower() or _infer_selector_type(value)[0]
    page = browser._page
    if page is None:
        return False
    try:
        if sel_type == "css":
            await page.locator(value).first.fill(text, timeout=6000)
            return True
        if sel_type == "label":
            await page.get_by_label(value).first.fill(text, timeout=6000)
            return True
        if sel_type == "role":
            role, _, name = value.partition(":")
            await page.get_by_role(role.strip() or "textbox", name=name.strip()).first.fill(
                text, timeout=6000,
            )
            return True
        if sel_type == "text":
            # Fill via placeholder text or the closest input.
            try:
                await page.get_by_placeholder(value).first.fill(text, timeout=4000)
                return True
            except Exception:
                pass
            # Fallback: find input near a label-like text.
            await page.get_by_label(value).first.fill(text, timeout=4000)
            return True
    except Exception as exc:
        logger.warning("fill(%s=%r) failed: %s", sel_type, value, exc)
    return False
