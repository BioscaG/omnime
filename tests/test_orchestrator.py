"""Orchestrator routing and intent classification (legacy + tuple)."""
from __future__ import annotations

import json

import pytest

from src.brain.llm_client import ToolCall
from src.brain.orchestrator import Intent, Orchestrator


@pytest.mark.asyncio
async def test_classify_intent_chat(fake_llm, memory_manager):
    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="CHAT", input={})]
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    intent, _meta = await orch.classify_intent("How are you?")
    assert intent == Intent.CHAT


@pytest.mark.asyncio
async def test_classify_intent_unknown_falls_back(fake_llm, memory_manager):
    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="MYSTERY", input={})]
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    intent, _meta = await orch.classify_intent("???")
    assert intent == Intent.CHAT


@pytest.mark.asyncio
async def test_process_message_store_path(fake_llm, memory_manager):
    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="STORE", input={})]
    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [{"name": "Demo project"}],
        "work_experience": [], "education": [], "skills": [],
        "contacts": [], "achievements": [], "life_events": [],
        "ideas": [], "user_profile_updates": {},
    })
    user_id = memory_manager.ensure_user(telegram_id=10)
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    response = await orch.process_message(user_id, "I started Demo project")
    assert response.intent == Intent.STORE
    assert "Demo project" in response.text


@pytest.mark.asyncio
async def test_process_message_chat_path(fake_llm, memory_manager):
    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="CHAT", input={})]
    fake_llm.default = "Sure, happy to chat."
    user_id = memory_manager.ensure_user(telegram_id=11)
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    response = await orch.process_message(user_id, "Hi friend")
    assert response.intent == Intent.CHAT
    assert response.text == "Sure, happy to chat."
