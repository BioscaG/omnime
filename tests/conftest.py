"""Shared fixtures for the test suite."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

import src.memory.db as db_module
from src.memory.models import Base


# Map JSONB → JSON when running on SQLite. Registered once per process.
@JSONB.compile.dispatch.for_type(JSONB)  # type: ignore[attr-defined]
def _jsonb_to_json(element, compiler, **kw):
    return compiler.visit_JSON(JSON(), **kw)


@pytest.fixture(autouse=True)
def _sqlite_in_memory(monkeypatch):
    """Replace the postgres engine with a fresh sqlite in-memory engine."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionLocal", SessionLocal)
    yield
    Base.metadata.drop_all(engine)


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class FakeLLM:
    """LLM stub: scripted responses keyed by substring + tool/stream support."""

    def __init__(self, responses: dict[str, str] | None = None, default: str = "OK") -> None:
        self.responses = responses or {}
        self.default = default
        self.calls: list[dict[str, Any]] = []
        self.tool_calls_to_return: list[Any] = []
        self.usage = type("U", (), {"as_dict": lambda self: {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "calls": 0, "cost_usd": 0.0,
        }})()
        self.provider = "anthropic"

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        model_tier: str = "fast",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        cache: bool = False,
        cache_system: bool = True,
        force_provider: str | None = None,
    ) -> str:
        self.calls.append({
            "prompt": prompt, "system": system, "tier": model_tier,
            "force_provider": force_provider,
        })
        for key, value in self.responses.items():
            if key.lower() in prompt.lower():
                return value
        return self.default

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        model_tier: str = "fast",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        cache_system: bool = True,
    ) -> AsyncIterator[str]:
        text = await self.complete(prompt, system, model_tier, max_tokens, temperature)
        # Stream as small chunks to mimic real provider behaviour.
        for piece in [text[i:i + 16] for i in range(0, len(text), 16)] or [""]:
            yield piece

    async def use_tools(
        self,
        prompt: str,
        tools: list,
        system: str | None = None,
        model_tier: str = "fast",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        tool_choice: str | None = None,
        cache_system: bool = True,
        cache_tools: bool = True,
    ):
        from src.brain.llm_client import ToolCall, ToolUseResult

        if self.tool_calls_to_return:
            return ToolUseResult(text="", tool_calls=list(self.tool_calls_to_return), stop_reason="tool_use")
        # Default: pick the tool whose name shows up first in the prompt
        # (after the user message), else the first tool.
        for t in tools:
            if t.name in prompt:
                return ToolUseResult(
                    text="",
                    tool_calls=[ToolCall(id="0", name=t.name, input={})],
                    stop_reason="tool_use",
                )
        return ToolUseResult(
            text="",
            tool_calls=[ToolCall(id="0", name=tools[-1].name, input={})],
            stop_reason="tool_use",
        )

    async def describe_image(self, path, prompt: str = "") -> str:
        return self.responses.get("image", "an image")


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest_asyncio.fixture
async def memory_manager(fake_llm):
    from src.memory.manager import MemoryManager
    from src.memory.semantic import SemanticStore

    return MemoryManager(llm=fake_llm, semantic=SemanticStore(in_memory=True))
