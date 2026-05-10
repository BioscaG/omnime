"""Orchestrator schedules a profile refresh after meaningful STORE intents."""
from __future__ import annotations

import asyncio
import json

import pytest

from src.brain.llm_client import ToolCall
from src.brain.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_meaningful_store_triggers_profile_refresh(fake_llm, memory_manager):
    fake_llm.tool_calls_to_return = [ToolCall(id="0", name="STORE", input={})]
    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [{"name": "Bridge"}],
        "work_experience": [{"company": "Acme", "role": "Engineer"}],
        "education": [], "skills": [],
        "contacts": [], "achievements": [],
        "life_events": [], "ideas": [],
        "user_profile_updates": {"name": "Guido"},
    })

    refresh_calls: list[int] = []

    async def fake_refresh(user_id):
        refresh_calls.append(user_id)
        return "profile updated"

    memory_manager.summarizer.update_living_profile = fake_refresh
    user_id = memory_manager.ensure_user(telegram_id=1)
    orch = Orchestrator(llm=fake_llm, memory=memory_manager)
    await orch.process_message(user_id, "I just changed jobs to Acme as an Engineer")
    # Yield control so the scheduled refresh task runs.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert refresh_calls == [user_id]
