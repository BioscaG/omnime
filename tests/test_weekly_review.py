"""Weekly review skill renders JSON + persists row."""
from __future__ import annotations

import json

import pytest

from src.brain.context_builder import Context
from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.weekly_review import WeeklyReviewSkill


REVIEW_PAYLOAD = {
    "wins": ["shipped feature X", "good 1:1 with Sarah"],
    "stuck": ["RFC stalling"],
    "goals_next_week": ["draft v2 of RFC"],
    "reflection_questions": ["what energised you?", "what drained you?", "what to drop?"],
    "tone": "supportive but direct",
    "summary": "A solid week with one persistent block.",
}


@pytest.mark.asyncio
async def test_review_persists_and_formats(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1, name="Guido")
    fake_llm.default = json.dumps(REVIEW_PAYLOAD)
    skill = WeeklyReviewSkill(fake_llm, memory_manager)

    ctx = Context(user_id=user_id, profile={"name": "Guido"})
    sr = await skill.execute("/review", ctx)

    assert "Wins" in sr.text and "shipped feature X" in sr.text
    assert sr.metadata["review"]["wins"][0] == "shipped feature X"
    with session_scope() as s:
        row = StructuredStore(s).latest_weekly_review(user_id)
        assert row is not None
        assert row.wins == REVIEW_PAYLOAD["wins"]


@pytest.mark.asyncio
async def test_review_handles_invalid_json(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=2)
    fake_llm.default = "not json at all"
    skill = WeeklyReviewSkill(fake_llm, memory_manager)
    ctx = Context(user_id=user_id, profile={})
    sr = await skill.execute("/review", ctx)
    assert "malformed JSON" in sr.text
