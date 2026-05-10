"""/forget command writes audit entries and removes the structured row."""
from __future__ import annotations

import pytest

from src.memory.db import session_scope
from src.memory.structured import StructuredStore


@pytest.mark.asyncio
async def test_forget_project_audits_and_deletes(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=1, name="Guido")
    with session_scope() as s:
        StructuredStore(s).upsert_project(user_id=user_id, name="ATLAS", status="active")

    msg = memory_manager.forget(user_id, "project ATLAS")
    assert "Forgot 1" in msg

    with session_scope() as s:
        store = StructuredStore(s)
        assert store.list_projects(user_id) == []
        audit = store.list_audit(user_id)
        assert any(a.action == "forget" and a.entity_type == "project" for a in audit)


@pytest.mark.asyncio
async def test_forget_unknown_returns_no_match(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=2)
    msg = memory_manager.forget(user_id, "project NotHere")
    assert "No project matched" in msg


@pytest.mark.asyncio
async def test_forget_invalid_selector(memory_manager):
    user_id = memory_manager.ensure_user(telegram_id=3)
    msg = memory_manager.forget(user_id, "nonsense")
    assert "Usage" in msg or "Empty" in msg
