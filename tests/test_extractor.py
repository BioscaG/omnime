"""Entity extractor parses LLM JSON correctly."""
from __future__ import annotations

import json

import pytest

from src.memory.extractor import EntityExtractor


@pytest.mark.asyncio
async def test_extract_parses_json(fake_llm):
    payload = {
        "projects": [{"name": "X"}],
        "work_experience": [],
        "education": [],
        "skills": [],
        "contacts": [{"name": "Sarah Chen"}],
        "achievements": [],
        "life_events": [],
        "ideas": [],
        "user_profile_updates": {},
    }
    fake_llm.default = json.dumps(payload)
    ex = await EntityExtractor(fake_llm).extract("I met Sarah and started project X")
    assert not ex.is_empty()
    assert ex.projects[0]["name"] == "X"
    assert ex.contacts[0]["name"] == "Sarah Chen"


@pytest.mark.asyncio
async def test_extract_handles_code_fences(fake_llm):
    fake_llm.default = "```json\n" + json.dumps({
        "projects": [], "work_experience": [], "education": [], "skills": [],
        "contacts": [], "achievements": [], "life_events": [], "ideas": [],
        "user_profile_updates": {"name": "Alice"},
    }) + "\n```"
    ex = await EntityExtractor(fake_llm).extract("I'm Alice")
    assert ex.user_profile_updates.get("name") == "Alice"


@pytest.mark.asyncio
async def test_extract_returns_empty_on_invalid_json(fake_llm):
    fake_llm.default = "not json at all"
    ex = await EntityExtractor(fake_llm).extract("hello")
    assert ex.is_empty()
