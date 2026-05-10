"""LLM client: prompt-caching system blocks and tool-use serialization."""
from __future__ import annotations

from typing import Any

import pytest

from src.brain.llm_client import LLMClient, ToolDef


def test_system_blocks_marks_cache_control():
    blocks = LLMClient._system_blocks("You are a coach.", cache_system=True)
    assert blocks is not None
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_system_blocks_omitted_when_disabled():
    blocks = LLMClient._system_blocks("hi", cache_system=False)
    assert blocks == [{"type": "text", "text": "hi"}]


def test_estimate_cost_returns_zero_for_unknown_model():
    assert LLMClient._estimate_cost("unknown-model", 1000, 1000, 0, 0) == 0.0


def test_estimate_cost_honours_cache_pricing():
    """1M cache-read tokens should be markedly cheaper than 1M fresh-input tokens."""
    cache_only = LLMClient._estimate_cost(
        "claude-sonnet-4-20250514", inp=0, out=0, cw=0, cr=1_000_000
    )
    fresh_only = LLMClient._estimate_cost(
        "claude-sonnet-4-20250514", inp=1_000_000, out=0, cw=0, cr=0
    )
    assert cache_only < fresh_only


def test_tooldef_input_schema_passthrough():
    t = ToolDef(name="X", description="test", input_schema={"type": "object", "properties": {}})
    assert t.input_schema["type"] == "object"
