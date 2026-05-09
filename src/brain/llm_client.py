"""Multi-provider LLM client with retries, fallback and tier-based model selection."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Literal, Optional

from cachetools import TTLCache
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings


logger = logging.getLogger(__name__)


ModelTier = Literal["fast", "powerful"]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, inp: int, out: int) -> None:
        self.input_tokens += inp
        self.output_tokens += out
        self.calls += 1


class LLMError(Exception):
    pass


class LLMClient:
    """Provider-agnostic LLM client.

    Supports anthropic, openai and ollama. Retries with exponential backoff,
    falls back to a secondary provider on terminal failure, and tracks token usage.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model_fast: Optional[str] = None,
        model_powerful: Optional[str] = None,
        fallback_provider: Optional[str] = None,
        fallback_model: Optional[str] = None,
        cache_ttl: int = 60 * 10,
        cache_size: int = 256,
    ) -> None:
        self.provider = provider or settings.llm_provider
        self.model_fast = model_fast or settings.llm_model_fast
        self.model_powerful = model_powerful or settings.llm_model_powerful
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
    ) -> str:
        cache_key = None
        if cache:
            cache_key = self._cache_key(prompt, system, model_tier, max_tokens, temperature)
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            text = await self._complete_with_retry(
                provider=self.provider,
                model=self._model_for(model_tier),
                prompt=prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as primary_exc:
            if self.fallback_provider and self.fallback_model:
                logger.warning(
                    "Primary provider %s failed (%s) — using fallback %s",
                    self.provider, primary_exc, self.fallback_provider,
                )
                text = await self._complete_with_retry(
                    provider=self.fallback_provider,
                    model=self.fallback_model,
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
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
    ) -> AsyncIterator[str]:
        async for chunk in self._stream(
            provider=self.provider,
            model=self._model_for(model_tier),
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            yield chunk

    # --- Internals -----------------------------------------------------
    def _model_for(self, tier: ModelTier) -> str:
        return self.model_powerful if tier == "powerful" else self.model_fast

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
    ) -> str:
        if provider == "anthropic":
            return await self._call_anthropic(model, prompt, system, max_tokens, temperature)
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
    ) -> AsyncIterator[str]:
        if provider == "anthropic":
            async for c in self._stream_anthropic(model, prompt, system, max_tokens, temperature):
                yield c
            return
        # Fallback: yield full response in one chunk for non-streaming providers
        text = await self._call(provider, model, prompt, system, max_tokens, temperature)
        yield text

    async def _call_anthropic(
        self, model: str, prompt: str, system: str | None,
        max_tokens: int, temperature: float,
    ) -> str:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = await client.messages.create(**kwargs)
        text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        if hasattr(resp, "usage"):
            self.usage.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return text

    async def _stream_anthropic(
        self, model: str, prompt: str, system: str | None,
        max_tokens: int, temperature: float,
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        async with client.messages.stream(**kwargs) as stream:
            async for chunk in stream.text_stream:
                yield chunk

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
