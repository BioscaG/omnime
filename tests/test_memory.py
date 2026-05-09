"""Memory system smoke tests."""
from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_ensure_user_creates_profile(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=123, name="Alice")
    assert user_id > 0
    again = memory_manager.ensure_user(telegram_id=123)
    assert again == user_id


@pytest.mark.asyncio
async def test_log_message_and_recent(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=42)
    memory_manager.log_message(user_id=user_id, text="hello", role="user")
    memory_manager.log_message(user_id=user_id, text="hi back", role="assistant")
    recent = memory_manager.recent_messages(user_id, limit=5)
    assert len(recent) == 2
    assert recent[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_process_and_store_extracts_project(fake_llm, memory_manager):
    fake_llm.responses["return only valid json"] = json.dumps({
        "projects": [{
            "name": "ATLAS",
            "description": "Personal AI assistant",
            "technologies": ["Python", "PostgreSQL"],
            "status": "active",
        }],
        "work_experience": [],
        "education": [],
        "skills": [{"name": "Python", "proficiency": "advanced"}],
        "contacts": [],
        "achievements": [],
        "life_events": [],
        "ideas": [],
        "user_profile_updates": {},
    })
    user_id = memory_manager.ensure_user(telegram_id=7)
    result = await memory_manager.process_and_store(user_id, "I started ATLAS in Python")
    assert not result.extraction.is_empty()
    profile = memory_manager.get_user_profile(user_id)
    names = [p["name"] for p in profile["projects"]]
    assert "ATLAS" in names
    assert any(s["name"] == "Python" for s in profile["skills"])
