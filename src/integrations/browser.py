"""Playwright wrapper used by the browser-agent skill.

Each `Browser` instance owns one Chromium (or Firefox) context. Methods are
async and return useful summaries (page title, URL, screenshot bytes) for
the orchestrator to feed back to Claude.

Set ``BROWSER_ENGINE=firefox`` in env to switch from Chromium to Firefox
(useful when a target site fingerprints Chromium aggressively).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger(__name__)


# Heuristic markers that indicate the user is about to do something irreversible.
PAYMENT_PATTERNS = re.compile(
    r"\b(pay\s*now|complete\s*purchase|confirm\s*payment|pagar|finalizar|"
    r"comprar|checkout|place\s*order|3d\s*secure)\b",
    re.I,
)


@dataclass
class PageState:
    url: str = ""
    title: str = ""
    text_excerpt: str = ""
    screenshot_path: Optional[Path] = None
    looks_like_payment: bool = False
    interactive_elements: list[dict[str, Any]] = field(default_factory=list)


class Browser:
    """Async Chromium controller. Singleton-friendly per session."""

    def __init__(self, headless: bool = True, viewport: tuple[int, int] = (1280, 800)) -> None:
        self.headless = headless
        self.viewport = viewport
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def start(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Playwright not installed. Add it to requirements and run "
                "`playwright install chromium` in the container."
            ) from exc

        engine = (os.getenv("BROWSER_ENGINE") or "chromium").lower()
        self._playwright = await async_playwright().start()
        if engine == "firefox":
            self._browser = await self._playwright.firefox.launch(headless=self.headless)
        else:
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
        self._context = await self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        # Hide the navigator.webdriver flag — Playwright sets it by default,
        # which trivial bot detectors check for.
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self._page = await self._context.new_page()

    async def close(self) -> None:
        try:
            if self._page is not None:
                await self._page.close()
        finally:
            self._page = None
        try:
            if self._context is not None:
                await self._context.close()
        finally:
            self._context = None
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    # --- Navigation -----------------------------------------------------
    async def goto(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout: float = 45000,
    ) -> None:
        """Navigate with a forgiving wait strategy.

        ``networkidle`` never resolves on tracker-heavy SPAs (Renfe, LinkedIn,
        booking sites). ``domcontentloaded`` returns once the DOM is ready;
        we then sleep a short tick to let the initial JS paint before the
        screenshot is taken."""
        await self.start()
        try:
            await self._page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as exc:
            # Tolerate timeouts: the page may still be usable (lazy assets
            # haven't finished but the main content is there).
            logger.warning("goto(%s) timeout/error: %s — continuing", url, exc)
        # Give the SPA a moment to render before the next screenshot.
        try:
            await self._page.wait_for_load_state("load", timeout=8000)
        except Exception:
            pass
        await asyncio.sleep(1.5)

    async def click_text(self, text: str, timeout: float = 5000) -> bool:
        await self.start()
        try:
            await self._page.get_by_text(text, exact=False).first.click(timeout=timeout)
            return True
        except Exception as exc:
            logger.warning("click_text(%r) failed: %s", text, exc)
            return False

    async def click_role(self, role: str, name: str, timeout: float = 5000) -> bool:
        await self.start()
        try:
            await self._page.get_by_role(role, name=name).first.click(timeout=timeout)
            return True
        except Exception as exc:
            logger.warning("click_role(%s, %r) failed: %s", role, name, exc)
            return False

    async def fill(self, selector: str, value: str, timeout: float = 5000) -> bool:
        await self.start()
        try:
            await self._page.locator(selector).first.fill(value, timeout=timeout)
            return True
        except Exception as exc:
            logger.warning("fill(%s) failed: %s", selector, exc)
            return False

    async def fill_label(self, label: str, value: str, timeout: float = 5000) -> bool:
        await self.start()
        try:
            await self._page.get_by_label(label).first.fill(value, timeout=timeout)
            return True
        except Exception as exc:
            logger.warning("fill_label(%r) failed: %s", label, exc)
            return False

    async def press(self, key: str) -> None:
        await self.start()
        await self._page.keyboard.press(key)

    async def scroll(self, dy: int = 600) -> None:
        await self.start()
        await self._page.mouse.wheel(0, dy)

    async def wait(self, seconds: float) -> None:
        await asyncio.sleep(min(max(0.0, seconds), 30.0))

    # --- Inspection -----------------------------------------------------
    async def state(self, screenshot_dir: Path) -> PageState:
        """Capture page state with tight timeouts so a hung page can never
        block the whole agent loop. Every sub-operation is wrapped in
        ``asyncio.wait_for``: failures degrade gracefully instead of bubbling."""
        await self.start()
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"step_{int(asyncio.get_event_loop().time() * 1000)}.png"
        # If the body is empty after 4s, ship whatever screenshot we can.
        try:
            await self._page.wait_for_function(
                "() => document.body && document.body.innerText.length > 30",
                timeout=4000,
            )
        except Exception:
            pass
        try:
            await self._page.screenshot(path=str(path), full_page=False, timeout=8000)
        except Exception as exc:
            logger.warning("screenshot failed: %s", exc)
            path = None

        url = self._page.url
        try:
            title = await asyncio.wait_for(self._page.title(), timeout=3.0)
        except Exception:
            title = ""
        try:
            body_text = await asyncio.wait_for(
                self._page.evaluate("() => document.body && document.body.innerText"),
                timeout=4.0,
            )
        except Exception:
            body_text = ""
        excerpt = (body_text or "")[:2000]

        elements: list[dict[str, Any]] = []
        try:
            elements = await asyncio.wait_for(
                self._page.evaluate(
                    """() => {
                        const items = [];
                        const sel = 'a, button, input, textarea, select, [role=\"button\"], [role=\"link\"]';
                        document.querySelectorAll(sel).forEach((el, idx) => {
                            if (idx > 60) return;
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) return;
                            items.push({
                                tag: el.tagName.toLowerCase(),
                                role: el.getAttribute('role') || null,
                                type: el.getAttribute('type') || null,
                                name: el.getAttribute('name') || null,
                                id: el.id || null,
                                label: el.getAttribute('aria-label') || null,
                                placeholder: el.getAttribute('placeholder') || null,
                                text: (el.innerText || '').slice(0, 80),
                            });
                        });
                        return items;
                    }"""
                ),
                timeout=5.0,
            )
        except Exception as exc:
            logger.debug("element scan failed: %s", exc)

        return PageState(
            url=url,
            title=title or "",
            text_excerpt=excerpt,
            screenshot_path=path,
            looks_like_payment=bool(PAYMENT_PATTERNS.search(excerpt)),
            interactive_elements=elements,
        )

    async def screenshot_bytes(self) -> bytes:
        await self.start()
        return await self._page.screenshot(full_page=False)
