"""Shared fixtures for the test suite."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.memory.db as db_module
from src.memory.models import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _sqlite_in_memory(monkeypatch):
    """Replace the postgres engine with sqlite in-memory for tests."""
    engine = create_engine("sqlite:///:memory:", future=True)

    # Skip JSONB/PG-only types when using SQLite
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy import JSON

    @JSONB.compile.dispatch.for_type(JSONB)  # type: ignore
    def _jsonb_to_json(element, compiler, **kw):
        return compiler.visit_JSON(JSON(), **kw)

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionLocal", SessionLocal)
    yield
    Base.metadata.drop_all(engine)


class FakeLLM:
    """LLM stub that returns scripted responses based on substring matching."""

    def __init__(self, responses: dict[str, str] | None = None, default: str = "OK") -> None:
        self.responses = responses or {}
        self.default = default
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        model_tier: str = "fast",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        cache: bool = False,
    ) -> str:
        self.calls.append(
            {"prompt": prompt, "system": system, "tier": model_tier}
        )
        for key, value in self.responses.items():
            if key.lower() in prompt.lower():
                return value
        return self.default


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest_asyncio.fixture
async def memory_manager(fake_llm):
    from src.memory.manager import MemoryManager
    from src.memory.semantic import SemanticStore

    return MemoryManager(llm=fake_llm, semantic=SemanticStore(in_memory=True))
