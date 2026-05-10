"""Tool-use intent routing + regex fast-path."""
from __future__ import annotations

import json

import pytest

from src.brain.llm_client import ToolCall
from src.brain.orchestrator import Intent, Orchestrator


@pytest.mark.asyncio
async def test_fastpath_routes_slash_cv_to_task(fake_llm, memory_manager):
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    intent, meta = await orch.classify_intent("/cv tailored to a backend role")
    assert intent == Intent.TASK
    assert meta["source"] == "regex"


@pytest.mark.asyncio
async def test_fastpath_routes_evolve_keyword(fake_llm, memory_manager):
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    intent, _ = await orch.classify_intent("add the ability to track expenses")
    assert intent == Intent.EVOLVE


@pytest.mark.asyncio
async def test_tool_use_routes_store(fake_llm, memory_manager):
    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="STORE", input={})]
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    intent, meta = await orch.classify_intent("I just shipped a new feature for OMNIME")
    assert intent == Intent.STORE
    assert meta["source"] == "tool_use"


@pytest.mark.asyncio
async def test_unknown_tool_falls_back_to_chat(fake_llm, memory_manager):
    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="MYSTERY", input={})]
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    intent, _ = await orch.classify_intent("hi there")
    assert intent == Intent.CHAT


@pytest.mark.asyncio
async def test_process_message_stores_via_tool_use(fake_llm, memory_manager):
    from src.brain.llm_client import ToolCall

    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="STORE", input={})]
    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [{"name": "Bridge"}],
        "work_experience": [], "education": [], "skills": [],
        "contacts": [], "achievements": [], "life_events": [],
        "ideas": [], "user_profile_updates": {},
    })
    user_id = memory_manager.ensure_user(telegram_id=10)
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    response = await orch.process_message(user_id, "I started a project called Bridge")
    assert response.intent == Intent.STORE
    assert "Bridge" in response.text


@pytest.mark.asyncio
async def test_private_routes_to_local_provider(fake_llm, memory_manager):
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    user_id = memory_manager.ensure_user(telegram_id=11)
    fake_llm.default = "local reply"
    response = await orch.process_message(user_id, "secret thoughts", private=True)
    assert response.metadata.get("private") is True
    assert any(c.get("force_provider") == "ollama" for c in fake_llm.calls)
