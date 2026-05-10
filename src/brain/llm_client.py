"""Multi-provider LLM client with retries, fallback, prompt caching and tool use."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional

from cachetools import TTLCache
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings


logger = logging.getLogger(__name__)


ModelTier = Literal["tiny", "fast", "powerful"]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    def add(
        self,
        inp: int,
        out: int,
        cache_creation: int = 0,
        cache_read: int = 0,
        cost: float = 0.0,
    ) -> None:
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_creation_tokens += cache_creation
        self.cache_read_tokens += cache_read
        self.calls += 1
        self.cost_usd += cost

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 4),
        }


# Indicative pricing per 1M tokens (USD). Update freely; only used to estimate
# the cost displayed by /usage. Not authoritative.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0, "output": 75.0,
        "cache_write": 18.75, "cache_read": 1.5,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "output": 15.0,
        "cache_write": 3.75, "cache_read": 0.3,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.0, "output": 5.0,
        "cache_write": 1.25, "cache_read": 0.10,
    },
}


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    cache_control: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolUseResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: Optional[str] = None
    raw_content: list[Any] = field(default_factory=list)
    """Raw provider content blocks (Anthropic). Needed to round-trip back as
    an ``assistant`` turn during a multi-step agentic loop, because the API
    requires the original ``tool_use`` blocks to match each ``tool_result``."""


class LLMError(Exception):
    pass


class LLMClient:
    """Provider-agnostic LLM client.

    Supports anthropic, openai and ollama. Adds:
      - prompt caching for the system block (Anthropic ephemeral cache)
      - tool use for structured routing
      - streaming text iterator
      - per-call cost estimation
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model_fast: Optional[str] = None,
        model_powerful: Optional[str] = None,
        model_tiny: Optional[str] = None,
        fallback_provider: Optional[str] = None,
        fallback_model: Optional[str] = None,
        cache_ttl: int = 60 * 10,
        cache_size: int = 256,
    ) -> None:
        self.provider = provider or settings.llm_provider
        self.model_fast = model_fast or settings.llm_model_fast
        self.model_powerful = model_powerful or settings.llm_model_powerful
        self.model_tiny = model_tiny or settings.llm_model_tiny
        self.fallback_provider = fallback_provider or settings.llm_fallback_provider
        self.fallback_model = fallback_model or settings.llm_fallback_model
        self.usage = TokenUsage()
        self._cache: TTLCache[str, str] = TTLCache(maxsize=cache_size, ttl=cache_ttl)

    # --- Public API ----------------------------------------------------
    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        model_tier: ModelTier = "fast",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        cache: bool = False,
        cache_system: bool = True,
        force_provider: str | None = None,
    ) -> str:
        """Single-turn completion.

        Parameters
        ----------
        cache_system : bool
            When True (default) and the provider is Anthropic, the system block
            is sent with `cache_control: {type: "ephemeral"}` for prompt caching.
        force_provider : str
            Override the configured provider for this call (e.g. route /private
            traffic to a local Ollama).
        """
        cache_key = None
        if cache:
            cache_key = self._cache_key(prompt, system, model_tier, max_tokens, temperature)
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        provider = force_provider or self.provider
        try:
            text = await self._complete_with_retry(
                provider=provider,
                model=self._model_for(model_tier),
                prompt=prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                cache_system=cache_system,
            )
        except Exception as primary_exc:
            if not force_provider and self.fallback_provider and self.fallback_model:
                logger.warning(
                    "Primary provider %s failed (%s) — using fallback %s",
                    provider, primary_exc, self.fallback_provider,
                )
                text = await self._complete_with_retry(
                    provider=self.fallback_provider,
                    model=self.fallback_model,
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    cache_system=False,
                )
            else:
                raise LLMError(str(primary_exc)) from primary_exc

        if cache_key is not None:
            self._cache[cache_key] = text
        return text

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        model_tier: ModelTier = "fast",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        cache_system: bool = True,
    ) -> AsyncIterator[str]:
        async for chunk in self._stream(
            provider=self.provider,
            model=self._model_for(model_tier),
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_system=cache_system,
        ):
            yield chunk

    async def use_tools(
        self,
        prompt: str,
        tools: list[ToolDef],
        system: str | None = None,
        model_tier: ModelTier = "fast",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        tool_choice: str | None = None,
        cache_system: bool = True,
        cache_tools: bool = True,
    ) -> ToolUseResult:
        """Anthropic tool-use call. OpenAI/Ollama get a JSON-mode fallback."""
        if self.provider == "anthropic":
            return await self._anthropic_tool_use(
                model=self._model_for(model_tier),
                prompt=prompt,
                tools=tools,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                tool_choice=tool_choice,
                cache_system=cache_system,
                cache_tools=cache_tools,
            )
        # Fallback: synthesise a single tool call by asking the model for JSON.
        text = await self.complete(
            prompt=(
                "Choose exactly one tool. Reply with JSON: "
                '{"tool": "<name>", "input": {...}}\nAvailable: '
                + json.dumps([{"name": t.name, "description": t.description} for t in tools])
                + "\n\n"
                + prompt
            ),
            system=system,
            model_tier=model_tier,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            data = json.loads(text)
            return ToolUseResult(
                text="",
                tool_calls=[ToolCall(id="0", name=data["tool"], input=data.get("input", {}))],
                stop_reason="tool_use",
            )
        except Exception:
            return ToolUseResult(text=text, tool_calls=[], stop_reason="end_turn")

    async def describe_image(self, path: Path, prompt: str = "Describe this image briefly.") -> str:
        """Multimodal description using Anthropic Claude (image as base64)."""
        if self.provider != "anthropic":
            return ""
        from anthropic import AsyncAnthropic

        data = base64.standard_b64encode(Path(path).read_bytes()).decode()
        media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=self.model_fast,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        if hasattr(resp, "usage"):
            self._record_usage(self.model_fast, resp.usage)
        return text

    # --- Internals -----------------------------------------------------
    def _model_for(self, tier: ModelTier) -> str:
        if tier == "powerful":
            return self.model_powerful
        if tier == "tiny":
            return self.model_tiny
        return self.model_fast

    @staticmethod
    def _cache_key(*parts: Any) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(str(p).encode("utf-8"))
            h.update(b"|")
        return h.hexdigest()

    async def _complete_with_retry(
        self,
        provider: str,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
        cache_system: bool,
    ) -> str:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        ):
            with attempt:
                return await self._call(
                    provider=provider,
                    model=model,
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    cache_system=cache_system,
                )
        raise LLMError("unreachable")  # pragma: no cover

    async def _call(
        self,
        provider: str,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
        cache_system: bool,
    ) -> str:
        if provider == "anthropic":
            return await self._call_anthropic(
                model, prompt, system, max_tokens, temperature, cache_system
            )
        if provider == "openai":
            return await self._call_openai(model, prompt, system, max_tokens, temperature)
        if provider == "ollama":
            return await self._call_ollama(model, prompt, system, max_tokens, temperature)
        raise LLMError(f"Unknown provider: {provider}")

    async def _stream(
        self,
        provider: str,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
        cache_system: bool,
    ) -> AsyncIterator[str]:
        if provider == "anthropic":
            async for c in self._stream_anthropic(
                model, prompt, system, max_tokens, temperature, cache_system
            ):
                yield c
            return
        text = await self._call(
            provider, model, prompt, system, max_tokens, temperature, cache_system=False
        )
        yield text

    @staticmethod
    def _system_blocks(system: str | None, cache_system: bool) -> list[dict[str, Any]] | str | None:
        if not system:
            return None
        if not cache_system or not settings.enable_prompt_caching:
            return system
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    @staticmethod
    def _supports_temperature(model: str) -> bool:
        """Reasoning-style Claude models (Opus 4.7+) reject the `temperature`
        parameter. We strip it for those and accept the model's default."""
        m = model.lower()
        # Known reasoning-only families that reject temperature.
        for marker in ("opus-4-7", "opus-4.7"):
            if marker in m:
                return False
        return True

    def _record_usage(self, model: str, usage: Any) -> None:
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = self._estimate_cost(model, inp, out, cw, cr)
        self.usage.add(inp, out, cache_creation=cw, cache_read=cr, cost=cost)

    @staticmethod
    def _estimate_cost(model: str, inp: int, out: int, cw: int, cr: int) -> float:
        prices = PRICING.get(model)
        if not prices:
            return 0.0
        return (
            inp * prices["input"]
            + out * prices["output"]
            + cw * prices.get("cache_write", prices["input"])
            + cr * prices.get("cache_read", prices["input"] * 0.1)
        ) / 1_000_000

    async def _call_anthropic(
        self,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
        cache_system: bool,
    ) -> str:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._supports_temperature(model):
            kwargs["temperature"] = temperature
        sys_blocks = self._system_blocks(system, cache_system)
        if sys_blocks is not None:
            kwargs["system"] = sys_blocks
        try:
            resp = await client.messages.create(**kwargs)
        except Exception as exc:
            err = str(exc).lower()
            # Cache_control rejected → retry with plain string system.
            if cache_system and "cache_control" in err and isinstance(sys_blocks, list):
                logger.warning("cache_control rejected; retrying without caching")
                kwargs["system"] = system
                resp = await client.messages.create(**kwargs)
            # Temperature rejected by an unknown reasoning model → strip and retry.
            elif "temperature" in err and "deprecated" in err and "temperature" in kwargs:
                logger.warning("Model %s rejects temperature; retrying without it", model)
                kwargs.pop("temperature", None)
                resp = await client.messages.create(**kwargs)
            else:
                raise
        text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        if hasattr(resp, "usage"):
            self._record_usage(model, resp.usage)
        return text

    async def _stream_anthropic(
        self,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
        cache_system: bool,
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._supports_temperature(model):
            kwargs["temperature"] = temperature
        sys_blocks = self._system_blocks(system, cache_system)
        if sys_blocks:
            kwargs["system"] = sys_blocks
        async with client.messages.stream(**kwargs) as stream:
            async for chunk in stream.text_stream:
                yield chunk
            final = await stream.get_final_message()
            if hasattr(final, "usage"):
                self._record_usage(model, final.usage)

    async def _anthropic_tool_use(
        self,
        model: str,
        prompt: str,
        tools: list[ToolDef],
        system: str | None,
        max_tokens: int,
        temperature: float,
        tool_choice: str | None,
        cache_system: bool,
        cache_tools: bool,
    ) -> ToolUseResult:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        tool_payload = []
        for i, t in enumerate(tools):
            entry: dict[str, Any] = {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            # Mark the LAST tool as the cache breakpoint so the whole tools
            # array is cached together.
            if cache_tools and i == len(tools) - 1:
                entry["cache_control"] = {"type": "ephemeral"}
            tool_payload.append(entry)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "tools": tool_payload,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._supports_temperature(model):
            kwargs["temperature"] = temperature
        sys_blocks = self._system_blocks(system, cache_system)
        if sys_blocks:
            kwargs["system"] = sys_blocks
        if tool_choice:
            kwargs["tool_choice"] = (
                {"type": "tool", "name": tool_choice}
                if tool_choice not in ("auto", "any", "none")
                else {"type": tool_choice}
            )

        resp = await client.messages.create(**kwargs)
        if hasattr(resp, "usage"):
            self._record_usage(model, resp.usage)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content: list[Any] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            raw_content.append(block)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input))
                )
        return ToolUseResult(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=getattr(resp, "stop_reason", None),
            raw_content=raw_content,
        )

    async def agentic_step(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[ToolDef],
        system: str | None = None,
        model_tier: ModelTier = "fast",
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> ToolUseResult:
        """One step of an agentic tool-use loop. Unlike ``use_tools`` (which
        takes a single string prompt), this accepts a full ``messages``
        array so the orchestrator can round-trip ``tool_use`` and
        ``tool_result`` blocks across turns.

        Anthropic only — providers without native tool-use don't support
        this loop shape and would degrade silently.
        """
        if self.provider != "anthropic":
            raise LLMError("agentic_step requires the Anthropic provider")

        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        model = self._model_for(model_tier)

        tool_payload = []
        for i, t in enumerate(tools):
            entry: dict[str, Any] = {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            if i == len(tools) - 1:
                entry["cache_control"] = {"type": "ephemeral"}
            tool_payload.append(entry)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "tools": tool_payload,
            "messages": messages,
        }
        if self._supports_temperature(model):
            kwargs["temperature"] = temperature
        sys_blocks = self._system_blocks(system, cache_system=True)
        if sys_blocks:
            kwargs["system"] = sys_blocks

        resp = await client.messages.create(**kwargs)
        if hasattr(resp, "usage"):
            self._record_usage(model, resp.usage)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_content: list[Any] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            raw_content.append(block)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input))
                )
        return ToolUseResult(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=getattr(resp, "stop_reason", None),
            raw_content=raw_content,
        )

    async def _call_openai(
        self, model: str, prompt: str, system: str | None,
        max_tokens: int, temperature: float,
    ) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if resp.usage:
            self.usage.add(resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return resp.choices[0].message.content or ""

    async def _call_ollama(
        self, model: str, prompt: str, system: str | None,
        max_tokens: int, temperature: float,
    ) -> str:
        import httpx

        url = f"{settings.ollama_host.rstrip('/')}/api/chat"
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [],
        }
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})
        async with httpx.AsyncClient(timeout=60.0) as http:
            r = await http.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            return data.get("message", {}).get("content", "")
