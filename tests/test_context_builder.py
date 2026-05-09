"""Context builder pulls data from memory."""
from __future__ import annotations

import json

import pytest

from src.brain.context_builder import ContextBuilder


@pytest.mark.asyncio
async def test_build_context_includes_recent_messages(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1)
    memory_manager.log_message(user_id=user_id, text="first message", role="user")
    memory_manager.log_message(user_id=user_id, text="second message", role="assistant")

    builder = ContextBuilder(memory_manager)
    ctx = await builder.build(user_id, "hello")
    assert ctx.user_id == user_id
    assert len(ctx.recent_messages) == 2


@pytest.mark.asyncio
async def test_to_prompt_block_renders_sections(memory_manager, fake_llm):
    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [{"name": "Alpha", "status": "active"}],
        "work_experience": [], "education": [],
        "skills": [{"name": "Python", "proficiency": "expert"}],
        "contacts": [], "achievements": [], "life_events": [],
        "ideas": [], "user_profile_updates": {"name": "Bob"},
    })
    user_id = memory_manager.ensure_user(telegram_id=2)
    await memory_manager.process_and_store(user_id, "Started project Alpha in Python")

    ctx = await ContextBuilder(memory_manager).build(user_id, "What's my project?")
    block = ctx.to_prompt_block()
    assert "ACTIVE PROJECTS" in block
    assert "Alpha" in block
