"""Coach skills: journaling, time machine."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from src.brain.context_builder import Context
from src.memory.db import session_scope
from src.memory.structured import StructuredStore
from src.skills.journaling import JournalingSkill
from src.skills.time_machine import TimeMachineSkill


@pytest.mark.asyncio
async def test_journaling_returns_prompt_when_empty(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1)
    fake_llm.default = "What did you choose to leave undone today?"
    skill = JournalingSkill(fake_llm, memory_manager)
    sr = await skill.execute("/journal", Context(user_id=user_id))
    assert "What did you choose" in sr.text
    assert sr.metadata["prompt"]


@pytest.mark.asyncio
async def test_journaling_logs_entry_with_sentiment(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=2)
    fake_llm.responses["score the emotional"] = "0.7"
    skill = JournalingSkill(fake_llm, memory_manager)
    sr = await skill.execute(
        "/journal really proud of shipping that feature today",
        Context(user_id=user_id),
    )
    assert "Logged" in sr.text
    assert sr.metadata["sentiment"] == 0.7


@pytest.mark.asyncio
async def test_time_machine_requires_date(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=3)
    skill = TimeMachineSkill(fake_llm, memory_manager)
    sr = await skill.execute("/timeline what was happening?", Context(user_id=user_id))
    assert "YYYY-MM-DD" in sr.text


@pytest.mark.asyncio
async def test_time_machine_summarises_snapshot(fake_llm, memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=4)
    with session_scope() as s:
        StructuredStore(s).upsert_work_experience(
            user_id=user_id, company="Acme", role="Engineer",
            start_date="2023-01-01", end_date=None,
        )
    fake_llm.default = "On 2025-06-01 you were at Acme as Engineer."
    skill = TimeMachineSkill(fake_llm, memory_manager)
    sr = await skill.execute("/timeline 2025-06-01", Context(user_id=user_id))
    assert "Acme" in sr.text
    assert sr.metadata["as_of"] == "2025-06-01"
