"""Goal tracking + check-in detection."""
from __future__ import annotations

from datetime import date

import pytest

from src.memory.db import session_scope
from src.memory.structured import StructuredStore


@pytest.mark.asyncio
async def test_add_goal_returns_payload(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1)
    g = memory_manager.add_goal(user_id, "run 3x a week")
    assert g["streak"] == 0
    assert g["description"] == "run 3x a week"


@pytest.mark.asyncio
async def test_check_in_increments_streak_on_consecutive_days(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=2)
    memory_manager.add_goal(user_id, "morning meditation daily")

    # Manually invoke the same path: check-in detector runs in process_and_store
    # but we want a unit test for the streak logic itself.
    with session_scope() as s:
        store = StructuredStore(s)
        goal = store.list_goals(user_id, status="active")[0]
        goal.last_check_in = date.today().fromordinal(date.today().toordinal() - 1)
        goal.streak = 1

    # Simulate today's check-in by calling the helper directly.
    memory_manager._maybe_register_goal_check_in(user_id, "I did my morning meditation today")

    with session_scope() as s:
        goal = StructuredStore(s).list_goals(user_id, status="active")[0]
        assert goal.streak == 2
        assert goal.last_check_in == date.today()
